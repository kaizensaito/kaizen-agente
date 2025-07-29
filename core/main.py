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

# --- Setup inicial ---
load_dotenv()
app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# --- Variáveis de ambiente ---
CLIENT_TZ = ZoneInfo("America/Sao_Paulo")
PORT = int(os.getenv("PORT", "10000"))

# Credenciais e tokens
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

# --- Configuração OpenAI e Gemini ---
openai_client = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)

# --- Sistema ---
SYSTEM_PROMPT = (
    "Você é o Kaizen: assistente autônomo, direto e levemente sarcástico, "
    "que busca a automelhoria, e provoca Nilson Saito e impulsiona a melhoria contínua."
)
MAX_CTX = 4000

# --- Logs e estado ---
logging.basicConfig(level=logging.INFO)
state_lock = threading.Lock()
state = {"last_heartbeat": None}

# --- Funções para chamadas LLM ---
def call_openai(model, text):
    try:
        resp = openai_client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": text}],
            temperature=0.7,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        raise RuntimeError(f"OpenAI[{model}] error: {e}")

def call_gemini(text):
    resp = genai.GenerativeModel("models/gemini-1.5-flash").generate_content([{"role": "user", "parts": [text]}])
    return getattr(resp, "text", "").strip()

def call_mistral(text):
    r = requests.post(
        "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.1",
        headers={"Authorization": f"Bearer {HF_TOKEN}"},
        json={"inputs": text},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()[0]["generated_text"].strip()

def call_openrouter(text):
    r = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OR_KEY}",
            "HTTP-Referer": "https://kaizen-agent",
            "X-Title": "Kaizen Agent",
        },
        json={
            "model": "mistral",
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": text}],
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()

def call_copilot(text):
    return call_openai("gpt-4o", text)

ALL_PROVIDERS = {}
if GEMINI_KEY:
    ALL_PROVIDERS["gemini"] = call_gemini
if HF_TOKEN:
    ALL_PROVIDERS["mistral"] = call_mistral
if OR_KEY:
    ALL_PROVIDERS["openrouter"] = call_openrouter
if OPENAI_KEY:
    ALL_PROVIDERS["gpt-3.5-turbo"] = lambda t: call_openai("gpt-3.5-turbo", t)
    ALL_PROVIDERS["copilot"] = call_copilot

# --- Cache e limites ---
fallback_order = list(ALL_PROVIDERS.keys())
usage_counters = {p: 0 for p in fallback_order}
DAILY_LIMITS = {"gemini": 50}
CACHE = {}
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
        "resposta": resp,
    })
    return resp

# --- Google Drive Memória ---
SCOPES = ["https://www.googleapis.com/auth/drive"]
MEM_FILE = "kaizen_memory_log.json"
MEMORY_LOCK = threading.Lock()

def drive_service():
    creds = service_account.Credentials.from_service_account_info(GOOGLE_CREDS, scopes=SCOPES)
    return build_drive("drive", "v3", credentials=creds, cache_discovery=False)

def get_file_id(svc):
    res = svc.files().list(
        q=f"name='{MEM_FILE}' and trashed=false",
        spaces="drive",
        fields="files(id,name)",
        pageSize=1,
    ).execute()
    files = res.get("files", [])
    return files[0]["id"] if files else None

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
        svc.files().update(fileId=fid, media_body=MediaIoBaseUpload(buf, "application/json")).execute()

# --- Notificações ---
def send_telegram(cid, txt):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": cid, "text": txt},
            timeout=10,
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
            to=f"whatsapp:{TWILIO_TO}",
        )
        logging.info("[whatsapp] enviado")
    except Exception as e:
        logging.error(f"[whatsapp] erro: {e}")

def send_email(subject, body):
    try:
        msg = MIMEMultipart()
        msg["From"] = msg["To"] = GMAIL_USER
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.send_message(msg)
        logging.info("[email] enviado")
    except Exception as e:
        logging.error(f"[email] erro: {e}")

# --- Loop autônomo: insight + notificações ---
def autonomous_loop():
    while True:
        try:
            insight = gerar_resposta_com_memoria("saito", "Gere um insight produtivo.")
            send_telegram(TELEGRAM_CHAT_ID, insight)
        except Exception:
            logging.exception("[auto] falhou")
        time.sleep(4 * 3600)  # A cada 4h

# --- Reset diário dos contadores ---
def reset_daily_counters():
    while True:
        now = datetime.now(timezone.utc)
        next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        time.sleep((next_midnight - now).total_seconds())
        for k in usage_counters:
            usage_counters[k] = 0
        logging.info("[quota] resetada")

# --- Heartbeat ---
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
    with state_lock:
        state["last_heartbeat"] = datetime.now(timezone.utc).isoformat()

# --- Reflexão diária ---
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

# --- Agendador loop ---
def schedule_loop():
    schedule.every().day.at("18:00").do(diario_reflexivo)
    schedule.every().hour.do(heartbeat_job)
    while True:
        schedule.run_pending()
        time.sleep(10)

# --- Busca produtos (ML, Shopee, Amazon) ---
def fetch_url_content(url, max_chars=5000):
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return r.text[:max_chars]
    except Exception as e:
        return f"❌ Erro ao buscar {url}: {e}"

def search_mercadolivre_api(query, limit=3):
    url = "https://api.mercadolibre.com/sites/MLB/search"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; KaizenBot/1.0; +https://kaizen)"}
    r = requests.get(url, params={"q": query, "limit": limit}, headers=headers, timeout=10)
    r.raise_for_status()
    return [
        {
            "site": "MercadoLivre",
            "title": item["title"],
            "price": f"R$ {item['price']:.2f}",
            "link": item["permalink"],
        }
        for item in r.json().get("results", [])[:limit]
    ]

def search_shopee_scrape(query, limit=3):
    url = f"https://shopee.com.br/search?keyword={requests.utils.quote(query)}"
    html = fetch_url_content(url, max_chars=200000)
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for item in soup.select("div._1NoI8_._16BAGk")[:limit]:
        title = item.get_text(strip=True)
        link = "https://shopee.com.br" + item.parent.get("href", "")
        items.append({"site": "Shopee", "title": title, "price": "Preço não disponível", "link": link})
    return items

def search_amazon_scrape(query, limit=3):
    url = f"https://www.amazon.com.br/s?k={requests.utils.quote(query)}"
    html = fetch_url_content(url, max_chars=200000)
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for item in soup.select("span.a-size-medium.a-color-base.a-text-normal")[:limit]:
        title = item.get_text(strip=True)
        # Amazon links são mais complexos, simplificado aqui
        items.append({"site": "Amazon", "title": title, "price": "Preço não disponível", "link": "https://www.amazon.com.br"})
    return items

# --- Rota Webhook com resposta inteligente e multi-IA fallback ---
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        texto = data.get("message", {}).get("text", "").strip()
        logging.info(f"[WEBHOOK] Mensagem recebida: {texto}")

        if texto.lower() in ("ping", "kaizen?", "eai kaizen?"):
            resp = "E aí, Saito? Tô aqui, firme e forte!"
        elif texto.lower().startswith("buscar "):
            query = texto[7:].strip()
            ml = search_mercadolivre_api(query)
            shopee = search_shopee_scrape(query)
            amazon = search_amazon_scrape(query)
            resp = f"Resultados para '{query}':\n\n"
            for res in ml + shopee + amazon:
                resp += f"- [{res['site']}] {res['title']} - {res['price']}\n  {res['link']}\n"
        else:
            resp = gerar_resposta_com_memoria("telegram", texto)

        # Resposta HTTP e log
        logging.info(f"[WEBHOOK] Resposta: {resp}")

        # Resposta simulada para Telegram (supondo webhook Telegram)
        return jsonify({"text": resp}), 200
    except Exception as e:
        logging.exception("[WEBHOOK] Erro:")
        return jsonify({"error": str(e)}), 500

# --- Rota status ---
@app.route("/", methods=["GET"])
def index():
    return jsonify({"status": "Kaizen online", "versao": "v4.0.0"}), 200

# --- Threads ---
threading.Thread(target=autonomous_loop, daemon=True).start()
threading.Thread(target=reset_daily_counters, daemon=True).start()
threading.Thread(target=schedule_loop, daemon=True).start()

# --- Main ---
if __name__ == "__main__":
    logging.info("🚀 Kaizen iniciando...")
    app.run(host="0.0.0.0", port=PORT)
