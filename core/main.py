# core/main.py

import os
import io
import json
import time
import threading
import logging
import requests
import schedule
import smtplib
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup

import google.generativeai as genai
from openai import OpenAI
from google.oauth2 import service_account
from googleapiclient.discovery import build as build_drive
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from twilio.rest import Client

# Load .env vars
load_dotenv()

# Constantes de ambiente
RENDER_API_KEY = os.getenv("RENDER_API_KEY") or "rnd_UVkvjr5wsRZ6pkkGitlrF9udmpCU"
RENDER_SERVICE_ID = os.getenv("RENDER_SERVICE_ID") or "srv-d1grusngi27c73c2gt3g"

# Função para deploy automático no Render
def deploy_automatico():
    url = f"https://api.render.com/v1/services/{RENDER_SERVICE_ID}/deploys"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {RENDER_API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        response = requests.post(url, headers=headers, json={})
        if response.status_code == 201:
            print("🚀 Deploy automático iniciado com sucesso!")
        else:
            print(f"❌ Erro ao iniciar deploy automático: {response.status_code}")
            print(response.text)
    except Exception as e:
        print(f"❌ Erro durante o deploy automático: {e}")

# Agendamento diário às 16:00
schedule.every().day.at("16:00").do(deploy_automatico)

# Thread para rodar o schedule em loop
threading.Thread(target=lambda: [schedule.run_pending() or time.sleep(60) for _ in iter(int, 1)], daemon=True).start()

# 🔎 Ferramenta para buscar conteúdo na web
def fetch_url_content(url, max_chars=5000):
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return r.text[:max_chars]
    except Exception as e:
        return f"❌ Erro ao buscar {url}: {e}"

# ── BUSCAS DE PRODUTO ───────────────────────────────────────────────────────────
def search_mercadolivre_api(query, limit=3):
    url = "https://api.mercadolibre.com/sites/MLB/search"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; KaizenBot/1.0; +https://kaizen)"
    }
    r = requests.get(url, params={"q": query, "limit": limit}, headers=headers, timeout=10)
    logging.info(f"[ML] Status: {r.status_code}")
    logging.info(f"[ML] URL chamada: {r.url}")
    logging.info(f"[ML] JSON: {json.dumps(r.json(), indent=2)[:1000]}")
    r.raise_for_status()
    return [
        {
            "site": "MercadoLivre",
            "title": item["title"],
            "price": f"R$ {item['price']:.2f}",
            "link": item["permalink"]
        }
        for item in r.json().get("results", [])[:limit]
    ]

def search_shopee_scrape(query, limit=3):
    url = f"https://shopee.com.br/search?keyword={requests.utils.quote(query)}"
    html = fetch_url_content(url, max_chars=200000)
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("div._1gkBDw")[:limit]
    out = []
    for c in cards:
        a = c.select_one("a._3NOHu2")
        if not a:
            continue
        title = a.get_text(strip=True)
        link = "https://shopee.com.br" + a["href"]
        whole = c.select_one("span._29R_un")
        dec = c.select_one("span._1qL5G9")
        price = (whole.get_text() + (dec.get_text() if dec else "")).replace(",", ".")
        out.append({"site": "Shopee", "title": title, "price": f"R$ {price}", "link": link})
    return out

def search_amazon_scrape(query, limit=3):
    headers = {"User-Agent": "Mozilla/5.0"}
    url = f"https://www.amazon.com.br/s?k={requests.utils.quote(query)}"
    html = fetch_url_content(url, max_chars=200000)
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("div.s-result-item[data-component-type='s-search-result']")[:limit]
    out = []
    for c in cards:
        t = c.select_one("span.a-size-medium.a-color-base.a-text-normal")
        p = c.select_one("span.a-offscreen")
        a = c.select_one("a.a-link-normal.a-text-normal, a.a-link-normal.s-no-outline")
        if not (t and p and a):
            continue
        out.append({
            "site": "Amazon",
            "title": t.get_text(strip=True),
            "price": p.get_text(strip=True),
            "link": "https://www.amazon.com.br" + a["href"]
        })
    return out

# ── NLU SIMPLES ────────────────────────────────────────────────────────────────
def is_product_query(text):
    return bool(re.search(r"\b(preciso|quero|comprar|valor|preço)\b", text.lower()))

def extract_product_name(text):
    patterns = [
        r"preciso (?:do|da|de)\s+(.+)",
        r"quero\s+comprar\s+(.+)",
        r"quero\s+(.+)",
        r"comprar\s+(.+)",
        r"valor (?:de|do|da)\s+(.+)",
        r"preço (?:de|do|da)\s+(.+)"
    ]
    tl = text.lower()
    for pat in patterns:
        m = re.search(pat, tl)
        if m:
            return m.group(1).strip()
    return text

# ⚙️ CONFIG
load_dotenv()
app = Flask(__name__)
CLIENT_TZ = ZoneInfo("America/Sao_Paulo")
MEMORY_LOCK = threading.Lock()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# 🔐 VARS
OPENAI_KEY         = os.getenv("OPENAI_API_KEY_MAIN")
GEMINI_KEY         = os.getenv("GEMINI_API_KEY")
HF_TOKEN           = os.getenv("HUGGINGFACE_API_TOKEN")
OR_KEY             = os.getenv("OPENROUTER_API_KEY")
GOOGLE_CREDS       = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON", "{}"))
TELEGRAM_TOKEN     = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN  = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM        = os.getenv("TWILIO_FROM_NUMBER")
TWILIO_TO          = os.getenv("TWILIO_TO_NUMBER")
GMAIL_USER         = os.getenv("GMAIL_USER")
GMAIL_PASS         = os.getenv("GMAIL_PASS")

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# 🤖 LLM WRAPPERS
openai_client = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)

SYSTEM_PROMPT = (
    "Você é o Kaizen: assistente autônomo, direto e levemente sarcástico, "
    "que provoca Nilson Saito e impulsiona a melhoria contínua."
)
MAX_CTX = 4000

def call_openai(model, text):
    try:
        resp = openai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": text}
            ],
            temperature=0.7
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        raise RuntimeError(f"OpenAI[{model}] error: {e}")

def call_gemini(text):
    resp = genai.GenerativeModel("models/gemini-1.5-flash").generate_content(
        [{"role": "user", "parts": [text]}]
    )
    return getattr(resp, "text", "").strip()

def call_mistral(text):
    r = requests.post(
        "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.1",
        headers={"Authorization": f"Bearer {HF_TOKEN}"},
        json={"inputs": text},
        timeout=30
    )
    r.raise_for_status()
    return r.json()[0]["generated_text"].strip()

def call_openrouter(text):
    r = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OR_KEY}",
            "HTTP-Referer": "https://kaizen-agent",
            "X-Title": "Kaizen Agent"
        },
        json={
            "model": "mistral",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": text}
            ]
        },
        timeout=30
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()

def call_copilot(text):
    return call_openai("gpt-4o", text)

ALL_PROVIDERS = {}
if GEMINI_KEY:      ALL_PROVIDERS["gemini"]       = call_gemini
if HF_TOKEN:        ALL_PROVIDERS["mistral"]      = call_mistral
if OR_KEY:          ALL_PROVIDERS["openrouter"]   = call_openrouter
if OPENAI_KEY:
    ALL_PROVIDERS["gpt-3.5-turbo"] = lambda t: call_openai("gpt-3.5-turbo", t)
    ALL_PROVIDERS["copilot"]       = call_copilot

# 🔁 FALLBACK + CACHE + CONTADORES
fallback_order = list(ALL_PROVIDERS.keys())
usage_counters = {p: 0 for p in fallback_order}
DAILY_LIMITS   = {"gemini": 50}  # Limite diário só para Gemini como exemplo
CACHE          = {}
_fallback_lock = threading.Lock()

def within_limit(provider):
    return usage_counters[provider] < DAILY_LIMITS.get(provider, float("inf"))

def gerar_resposta(text):
    global fallback_order
    with _fallback_lock:
        seq = fallback_order.copy()
    for prov in seq:
        if not within_limit(prov):
            continue
        try:
            logging.info(f"[fallback] tentando {prov}")
            out = CACHE.setdefault((prov, text), ALL_PROVIDERS[prov](text))
            if not out.strip():
                raise RuntimeError(f"{prov} retornou vazio")
            usage_counters[prov] += 1
            with _fallback_lock:
                fallback_order.remove(prov)
                fallback_order.insert(0, prov)
            return out
        except Exception as e:
            logging.warning(f"{prov} falhou: {e}")
    return "⚠️ Todas as IAs falharam."

def build_context(channel, msg):
    mem = read_memory()
    hist = [m for m in mem if m["origem"] == channel]
    parts, size = [], 0
    for h in reversed(hist):
        snippet = f"Usuário: {h['entrada']}\nKaizen: {h['resposta']}\n"
        if size + len(snippet) > MAX_CTX * 0.8:
            break
        parts.insert(0, snippet)
        size += len(snippet)
    parts.append(f"Usuário: {msg}")
    ctx = SYSTEM_PROMPT + "\n" + "".join(parts)
    return ctx[-MAX_CTX:] if len(ctx) > MAX_CTX else ctx

def gerar_resposta_com_memoria(channel, msg):
    resp = gerar_resposta(build_context(channel, msg))
    if resp.startswith("⚠️"):
        return resp
    write_memory({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "origem": channel,
        "entrada": msg,
        "resposta": resp
    })
    return resp

# 📁 GOOGLE DRIVE MEMÓRIA
SCOPES   = ['https://www.googleapis.com/auth/drive']
MEM_FILE = 'kaizen_memory_log.json'

def drive_service():
    creds = service_account.Credentials.from_service_account_info(GOOGLE_CREDS, scopes=SCOPES)
    return build_drive('drive', 'v3', credentials=creds, cache_discovery=False)

def get_file_id(svc):
    res = svc.files().list(
        q=f"name='{MEM_FILE}' and trashed=false",
        spaces='drive',
        fields='files(id,name)',
        pageSize=1
    ).execute()
    files = res.get('files', [])
    return files[0]['id'] if files else None

def read_memory():
    svc = drive_service()
    fid = get_file_id(svc)
    if not fid:
        logging.warning(f"[drive] '{MEM_FILE}' não encontrado — criando novo")
        meta = {"name": MEM_FILE}
        media = MediaIoBaseUpload(io.BytesIO(b"[]"), mimetype="application/json")
        svc.files().create(body=meta, media_body=media, fields="id").execute()
        return []
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, svc.files().get_media(fileId=fid))
    while True:
        done = dl.next_chunk()[1]
        if done:
            break
    buf.seek(0)
    return json.load(buf)

def write_memory(entry):
    with MEMORY_LOCK:
        svc = drive_service()
        fid = get_file_id(svc)
        mem = read_memory()
        mem.append(entry)
        buf = io.BytesIO(json.dumps(mem, indent=2).encode())
        svc.files().update(fileId=fid, media_body=MediaIoBaseUpload(buf, 'application/json')).execute()

# 📩 NOTIFICAÇÕES
def send_telegram(cid, txt):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": cid, "text": txt}
        )
        if not r.ok:
            logging.error(f"[telegram] {r.status_code}: {r.text}")
    except Exception:
        logging.exception("[telegram] erro")

def send_whatsapp(msg):
    try:
        twilio_client.messages.create(
            body=msg,
            from_=f"whatsapp:{TWILIO_FROM}",
            to=f"whatsapp:{TWILIO_TO}"
        )
        logging.info("[whatsapp] enviado")
    except Exception as e:
        logging.error(f"[whatsapp] erro: {e}")

def send_email(subject, body):
    try:
        msg = MIMEMultipart()
        msg['From'] = msg['To'] = GMAIL_USER
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.send_message(msg)
        logging.info("[email] enviado")
    except Exception as e:
        logging.error(f"[email] erro: {e}")

# 🔁 LOOPS AUTÔNOMOS
def autonomous_loop():
    while True:
        try:
            insight = gerar_resposta_com_memoria("saito", "Gere um insight produtivo.")
            send_telegram(TELEGRAM_CHAT_ID, insight)
        except Exception:
            logging.exception("[auto] falhou")
        time.sleep(4 * 3600)

def reset_daily_counters():
    while True:
        now = datetime.now(timezone.utc)
        nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        time.sleep((nxt - now).total_seconds())
        for k in usage_counters:
            usage_counters[k] = 0
        logging.info("[quota] resetada")

def heartbeat_job():
    logging.info("[heartbeat] executando")
    report = []
    for name, fn in ALL_PROVIDERS.items():
        ok = True
        try:
            fn("Teste Kaizen")
        except:
            ok = False
        report.append(f"{name}: {'OK' if ok else 'ERRO'}")
    text = f"Heartbeat {datetime.now(CLIENT_TZ).strftime('%Y-%m-%d %H:%M:%S')}\n" + "\n".join(report)
    send_whatsapp(text)
    send_telegram(TELEGRAM_CHAT_ID, text)
    send_email("Kaizen Heartbeat", text)

def diario_reflexivo():
    try:
        mem = read_memory()
        hoje = datetime.now(CLIENT_TZ).date()
        today_entries = [
            m for m in mem
            if datetime.fromisoformat(m["timestamp"]).astimezone(CLIENT_TZ).date() == hoje
        ]
        prompt = (
            "Você é o Kaizen. Com base nestas interações de hoje, gere uma reflexão "
            "sobre padrões de resposta, pontos fortes e onde posso melhorar:\n\n"
            + "\n".join(f"- {m['entrada']}" for m in today_entries)
        )
        resp = gerar_resposta(prompt)
        send_telegram(TELEGRAM_CHAT_ID, f"Reflexão diária:\n{resp}")
    except Exception:
        logging.exception("[reflexão] falhou")

def schedule_loop():
    schedule.every().day.at("18:00").do(diario_reflexivo)
    schedule.every().hour.do(heartbeat_job)
    while True:
        schedule.run_pending()
        time.sleep(10)

# ── ROTAS API ────────────────────────────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    msg = data.get("message", {}).get("text", "")
    chat_id = data.get("message", {}).get("chat", {}).get("id")
    if not msg or not chat_id:
        return jsonify({"error": "invalid payload"}), 400
    resp = gerar_resposta_com_memoria("telegram", msg)
    send_telegram(chat_id, resp)
    return jsonify({"status": "ok"})

@app.route("/search", methods=["GET"])
def search_api():
    q = request.args.get("q", "")
    if not q:
        return jsonify({"error": "missing query"}), 400
    if is_product_query(q):
        product = extract_product_name(q)
        ml = search_mercadolivre_api(product)
        sp = search_shopee_scrape(product)
        am = search_amazon_scrape(product)
        results = ml + sp + am
        return jsonify(results)
    return jsonify({"msg": "query não reconhecida para busca de produto"})

@app.route("/status", methods=["GET"])
def status():
    return jsonify({"status": "running", "time": datetime.now(CLIENT_TZ).isoformat()})

# ── INÍCIO ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Inicia loops autônomos em threads separadas
    threading.Thread(target=autonomous_loop, daemon=True).start()
    threading.Thread(target=reset_daily_counters, daemon=True).start()
    threading.Thread(target=schedule_loop, daemon=True).start()
    
    # Roda servidor Flask
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
