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

# Import dos módulos locais (garantir que os arquivos existam)
from modules.memory import read_memory, write_memory
from modules.llm import ALL_PROVIDERS, gerar_resposta, gerar_resposta_com_memoria
from modules.notify import send_telegram, send_whatsapp, send_email
from modules.fetcher import fetch_url_content
from modules.auto_learn import analisar_interacoes, carregar_aprendizado, salvar_aprendizado, registrar_log
from modules.critic import analisar_resposta, gerar_correcoes
from modules.planner import definir_objetivos, gerar_acoes, executar_acao, avaliar_resultado

# Configurações iniciais
load_dotenv()
app = Flask(__name__)
CLIENT_TZ = ZoneInfo("America/Sao_Paulo")
MEMORY_LOCK = threading.Lock()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Variáveis de ambiente
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

from twilio.rest import Client
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Sistema de Timezone e Datas
SCOPES = ['https://www.googleapis.com/auth/drive']
MEM_FILE = 'kaizen_memory_log.json'

# --- FUNÇÕES GOOGLE DRIVE PARA MEMÓRIA REMOTA ---
from google.oauth2 import service_account
from googleapiclient.discovery import build as build_drive
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

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
    # Mutex para evitar concorrência
    with MEMORY_LOCK:
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
        done = False
        while not done:
            status, done = dl.next_chunk()
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

# --- FUNÇÕES AUXILIARES ---

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

# --- BUSCAS DE PRODUTOS (API e Web scraping) ---

from bs4 import BeautifulSoup

def fetch_url_content(url, max_chars=5000):
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return r.text[:max_chars]
    except Exception as e:
        return f"❌ Erro ao buscar {url}: {e}"

def search_mercadolivre_api(query, limit=3):
    url = "https://api.mercadolibre.com/sites/MLB/search"
    r = requests.get(url, params={"q": query, "limit": limit}, timeout=10)
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

# --- LOOP AUTÔNOMO ---

def autonomous_loop():
    while True:
        try:
            # Exemplo: gerar insight automático e enviar telegram
            insight = gerar_resposta_com_memoria("saito", "Gere um insight produtivo.")
            send_telegram(TELEGRAM_CHAT_ID, insight)
        except Exception:
            logging.exception("[auto] falhou")
        time.sleep(4 * 3600)  # 4 horas

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
            + "\n".join(
                f"- Usuário: {e['entrada']}\n  Kaizen: {e['resposta']}"
                for e in today_entries[-10:]
            )
        )
        reflexao = gerar_resposta(prompt)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "origem": "diario",
            "entrada": prompt,
            "resposta": reflexao,
            "tipo": "reflexao"
        }
        write_memory(entry)
        send_telegram(TELEGRAM_CHAT_ID, f"🧠 Reflexão diária:\n{reflexao}")
        logging.info("[diario] reflexão criada e enviada")
    except Exception:
        logging.exception("[diario] falhou ao gerar reflexão")

# --- AGENDAMENTOS ---
schedule.every().day.at("18:00").do(heartbeat_job)
schedule.every().day.at("23:00").do(diario_reflexivo)

def schedule_loop():
    while True:
        schedule.run_pending()
        time.sleep(30)

# --- ROTAS FLASK ---

@app.route('/', methods=['GET'])
def index():
    return "OK", 200

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json(force=True)
    msg = data.get("message", "").strip()
    if not msg:
        return jsonify(error="mensagem vazia"), 400

    # /fetch ou /buscar
    if msg.lower().startswith(('/fetch ', '/buscar ')):
        url = msg.split(None, 1)[1]
        content = fetch_url_content(url)
        summary = gerar_resposta(f"Resuma este conteúdo da web:\n\n{content}")
        return jsonify(raw=content, summary=summary)

    # cotação (explícita ou natural)
    if msg.lower().startswith('/cotacao ') or is_product_query(msg):
        produto = (
            msg.split(None, 1)[1]
            if msg.lower().startswith('/cotacao ')
            else extract_product_name(msg)
        )
        cot = []
        cot += search_mercadolivre_api(produto)
        cot += search_shopee_scrape(produto)
        cot += search_amazon_scrape(produto)
        return jsonify(cotacao=cot)

    # fallback normal
    return jsonify(reply=gerar_resposta_com_memoria("web", msg))

@app.route('/usage', methods=['GET'])
def usage():
    return jsonify(usage_counters)

@app.route('/test_llm', methods=['GET'])
def test_llm():
    out = {}
    for name, fn in ALL_PROVIDERS.items():
        try:
            out[name] = {"ok": True, "reply": fn("Teste Kaizen")}
        except Exception as e:
            out[name] = {"ok": False, "error": str(e)}
    return jsonify(out)

@app.route('/telegram_webhook', methods=['POST'])
def telegram_webhook():
    payload = request.get_json(force=True).get("message", {})
    txt = payload.get("text", "").strip()
    cid = str(payload.get("chat", {}).get("id", ""))

    if not txt:
        send_telegram(cid, "⚠️ Mensagem vazia.")
        return jsonify(ok=True)

    # /fetch ou /buscar
    if txt.lower().startswith(('/fetch ', '/buscar ')):
        url = txt.split(None, 1)[1]
        content = fetch_url_content(url)
        summary = gerar_resposta(f"Resuma este conteúdo da web:\n\n{content}")
        send_telegram(cid, f"🔎 Conteúdo:\n{content[:500]}\n\n📝 Resumo:\n{summary}")
        return jsonify(ok=True)

    # cotação automática
    if txt.lower().startswith('/cotacao ') or is_product_query(txt):
        produto = (
            txt.split(None, 1)[1]
            if txt.lower().startswith('/cotacao ')
            else extract_product_name(txt)
        )
        cot = []
        cot += search_mercadolivre_api(produto)
        cot += search_shopee_scrape(produto)
        cot += search_amazon_scrape(produto)
        msg = f"📊 Cotação para *{produto}*:\n\n"
        for r in cot:
            msg += f"[{r['site']}] {r['title']}\n{r['price']}\n{r['link']}\n\n"
        send_telegram(cid, msg)
        return jsonify(ok=True)

    # resposta normal
    resp = gerar_resposta_com_memoria(f"tg:{cid}", txt)
    send_telegram(cid, resp)
    return jsonify(ok=True)

# --- THREADS PARA EXECUÇÃO PARARELA ---

threading.Thread(target=autonomous_loop, daemon=True).start()
threading.Thread(target=reset_daily_counters, daemon=True).start()
threading.Thread(target=schedule_loop, daemon=True).start()

# --- INICIALIZAÇÃO DO SERVIDOR FLASK ---

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
