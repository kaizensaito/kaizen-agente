# ⚙️ IMPORTS
import os, io, json, time, threading, logging, requests, schedule, smtplib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import google.generativeai as genai
from openai import OpenAI
from google.oauth2 import service_account
from googleapiclient.discovery import build as build_drive
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from twilio.rest import Client

# ⚙️ CONFIG
load_dotenv()
app = Flask(__name__)
CLIENT_TZ = ZoneInfo("America/Sao_Paulo")
MEMORY_LOCK = threading.Lock()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# 🔐 VARS
OPENAI_KEY       = os.getenv("OPENAI_API_KEY_MAIN")
GEMINI_KEY       = os.getenv("GEMINI_API_KEY")
HF_TOKEN         = os.getenv("HUGGINGFACE_API_TOKEN")
OR_KEY           = os.getenv("OPENROUTER_API_KEY")
GOOGLE_CREDS     = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON", "{}"))
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN   = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM         = os.getenv("TWILIO_FROM_NUMBER")
TWILIO_TO           = os.getenv("TWILIO_TO_NUMBER")
GMAIL_USER          = os.getenv("GMAIL_USER")
GMAIL_PASS          = os.getenv("GMAIL_PASS")

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# 🤖 LLMs
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
        return openai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": text}
            ],
            temperature=0.7
        ).choices[0].message.content.strip()
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

# 🔁 Fallback/caching
FALLBACK_ORDER   = [p for p in ["gemini","mistral","openrouter","gpt-3.5-turbo","copilot"] if p in ALL_PROVIDERS]
usage_counters   = {p: 0 for p in FALLBACK_ORDER}
DAILY_LIMITS     = {"gemini": 50}
CACHE            = {}
_fallback_lock   = threading.Lock()

def within_limit(provider):
    return usage_counters[provider] < DAILY_LIMITS.get(provider, float("inf"))

def cached(provider, fn, txt):
    return CACHE.setdefault((provider, txt), fn(txt))

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

def gerar_resposta(text):
    with _fallback_lock:
        seq = FALLBACK_ORDER.copy()
    for p in seq:
        if not within_limit(p):
            continue
        try:
            logging.info(f"[fallback] tentando {p}")
            out = cached(p, ALL_PROVIDERS[p], text)
            if not out.strip():
                raise RuntimeError(f"{p} vazio")
            usage_counters[p] += 1
            with _fallback_lock:
                FALLBACK_ORDER.remove(p)
                FALLBACK_ORDER.insert(0, p)
            return out
        except Exception as e:
            logging.warning(f"{p} falhou: {e}")
    return "⚠️ Todas as IAs falharam."

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

# 📁 Google Drive Memória
SCOPES   = ['https://www.googleapis.com/auth/drive']
MEM_FILE = 'kaizen_memory_log.json'

def drive_service():
    creds = service_account.Credentials.from_service_account_info(GOOGLE_CREDS, scopes=SCOPES)
    return build_drive('drive', 'v3', credentials=creds, cache_discovery=False)

def get_file_id(svc):
    res   = svc.files().list(
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
        logging.warning(f"[drive] '{MEM_FILE}' não encontrado, criando novo")
        meta  = {"name": MEM_FILE}
        media = MediaIoBaseUpload(io.BytesIO(b"[]"), mimetype="application/json")
        created = svc.files().create(body=meta, media_body=media, fields="id").execute()
        return []
    buf = io.BytesIO()
    dl  = MediaIoBaseDownload(buf, svc.files().get_media(fileId=fid))
    while True:
        done = dl.next_chunk()[1]
        if done: break
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

# 📩 Notificações
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
        msg['From']    = msg['To'] = GMAIL_USER
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.send_message(msg)
        logging.info("[email] enviado")
    except Exception as e:
        logging.error(f"[email] erro: {e}")

# 🔁 Loops autônomos
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
    for name in ALL_PROVIDERS:
        ok = True
        try:
            ALL_PROVIDERS[name]("Teste Kaizen")
        except:
            ok = False
        report.append(f"{name}: {'OK' if ok else 'ERRO'}")
    texto = (
        f"Heartbeat {datetime.now(CLIENT_TZ).strftime('%Y-%m-%d %H:%M:%S')}\n"
        + "\n".join(report)
    )
    send_whatsapp(texto)
    send_telegram(TELEGRAM_CHAT_ID, texto)
    send_email("Kaizen Heartbeat", texto)

# 📝 Reflexão diária autônoma
def diario_reflexivo():
    try:
        mem = read_memory()
        hoje = datetime.now(CLIENT_TZ).date()
        entradas_hoje = [
            m for m in mem
            if datetime.fromisoformat(m["timestamp"]).astimezone(CLIENT_TZ).date() == hoje
        ]
        prompt = (
            "Você é o Kaizen. Com base nestas interações de hoje, gere uma reflexão "
            "sobre padrões de resposta, pontos fortes e onde posso melhorar:\n\n"
            + "\n".join(
                f"- Usuário: {e['entrada']}\n  Kaizen: {e['resposta']}"
                for e in entradas_hoje[-10:]
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

# ⏰ Agendamentos
schedule.every().day.at("18:00").do(heartbeat_job)
schedule.every().day.at("23:00").do(diario_reflexivo)

def schedule_loop():
    while True:
        schedule.run_pending()
        time.sleep(30)

# 🌐 Rotas Flask
@app.route('/', methods=['GET'])
def index():
    return "OK", 200

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json(force=True)
    msg = data.get("message", "").strip()
    if not msg:
        return jsonify(error="mensagem vazia"), 400
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
    else:
        resp = gerar_resposta_com_memoria(f"tg:{cid}", txt)
        send_telegram(cid, resp)
    return jsonify(ok=True)

# ▶️ Inicia loops em background
threading.Thread(target=autonomous_loop, daemon=True).start()
threading.Thread(target=reset_daily_counters, daemon=True).start()
threading.Thread(target=schedule_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
