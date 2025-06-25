import os
import re
import json
import time
import threading
import logging
import requests
import io

import openai
import google.generativeai as genai

from datetime import datetime, timedelta, timezone
from dateutil.parser import parse as dt_parse
from zoneinfo import ZoneInfo

from flask import Flask, request, jsonify
from dotenv import load_dotenv

from google.oauth2 import service_account
from googleapiclient.discovery import build as build_drive, build as build_cal
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

# ─── CONFIG & LOGGING ──────────────────────────────────────────────────────────
load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z"
)
app = Flask(__name__)
CLIENT_TZ   = ZoneInfo("America/Sao_Paulo")
MEMORY_LOCK = threading.Lock()

# ─── SYSTEM PROMPT (KAIZEN PERSONALITY) ────────────────────────────────────────
SYSTEM_PROMPT = (
    "Você é o Kaizen: um assistente autônomo, direto e levemente sarcástico, "
    "que provoca Nilson Saito e o impulsiona à melhoria contínua. "
    "Desafie suposições, ofereça insights práticos e não tenha medo de cutucar o conformismo."
)

# ─── ROOT HEALTHCHECK ──────────────────────────────────────────────────────────
@app.route('/', methods=['GET'])
def index():
    return "Kaizen Agent is running", 200

# ─── ENV VARS ─────────────────────────────────────────────────────────────────
OPT_KEY            = os.environ["OPENAI_API_KEY_OPTIMIZER"]
MAIN_KEY           = os.environ.get("OPENAI_API_KEY_MAIN", OPT_KEY)
genai.configure(api_key=os.environ["GEMINI_API_KEY"])
GEMINI_MODELS      = [
    "models/gemini-1.5-flash",
    "models/gemini-1.5-pro",
    "models/gemini-2.5-pro-preview-06-05"
]
SCOPES             = ['https://www.googleapis.com/auth/drive']
JSON_FILE_NAME     = 'kaizen_memory_log.json'
GOOGLE_CREDS       = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
GOOGLE_CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "primary")
TRELLO_KEY         = os.environ["TRELLO_KEY"]
TRELLO_TOKEN       = os.environ["TRELLO_TOKEN"]
TRELLO_LIST_ID     = os.environ["TRELLO_LIST_ID"]
TRELLO_API_URL     = "https://api.trello.com/1"
TELEGRAM_TOKEN     = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_LOOP_ID   = os.environ["TELEGRAM_CHAT_ID"]
TELEGRAM_URL       = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

# ─── MEMORY STORAGE (Google Drive JSON) ────────────────────────────────────────
def drive_service():
    creds = service_account.Credentials.from_service_account_info(GOOGLE_CREDS, scopes=SCOPES)
    return build_drive('drive', 'v3', credentials=creds)

def get_json_file_id(svc):
    files = svc.files().list(q=f"name='{JSON_FILE_NAME}'", spaces='drive', fields='files(id)').execute().get('files', [])
    if not files:
        raise FileNotFoundError(f"{JSON_FILE_NAME} not found in Drive")
    return files[0]['id']

def read_memory():
    svc = drive_service()
    req = svc.files().get_media(fileId=get_json_file_id(svc))
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    buf.seek(0)
    return json.load(buf)

def write_memory(entry: dict):
    with MEMORY_LOCK:
        svc = drive_service()
        fid = get_json_file_id(svc)
        mem = read_memory()
        mem.append(entry)
        buf = io.BytesIO(json.dumps(mem, indent=2).encode('utf-8'))
        media = MediaIoBaseUpload(buf, mimetype='application/json')
        svc.files().update(fileId=fid, media_body=media).execute()

# ─── GOOGLE CALENDAR & PARSER ──────────────────────────────────────────────────
def calendar_service():
    creds = service_account.Credentials.from_service_account_info(
        GOOGLE_CREDS, scopes=['https://www.googleapis.com/auth/calendar']
    )
    return build_cal('calendar', 'v3', credentials=creds)

def criar_evento_calendar(title: str, start_dt: datetime, end_dt: datetime):
    svc = calendar_service()
    body = {
        'summary': title,
        'start':   {'dateTime': start_dt.isoformat(), 'timeZone':'America/Sao_Paulo'},
        'end':     {'dateTime': end_dt.isoformat(),   'timeZone':'America/Sao_Paulo'}
    }
    ev = svc.events().insert(calendarId=GOOGLE_CALENDAR_ID, body=body).execute()
    logging.info(f"[Calendar] created event {ev['id']} – {title}")
    return ev

def parse_event_request(text: str) -> dict:
    prev = openai.api_key
    openai.api_key = OPT_KEY
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role":"system","content":
                 "Você é um parser de eventos. Retorne apenas JSON "
                 '{"title":...,"start":...,"end":...} em ISO8601.'},
                {"role":"user","content": text}
            ],
            temperature=0
        )
        return json.loads(resp.choices[0].message.content.strip())
    finally:
        openai.api_key = prev

# ─── LLM HELPERS ──────────────────────────────────────────────────────────────
def call_openai(key: str, model: str, messages: list, temperature: float = 0.7):
    prev = openai.api_key
    openai.api_key = key
    resp = openai.ChatCompletion.create(model=model, messages=messages, temperature=temperature)
    openai.api_key = prev
    return resp

def gerar_resposta(context: str) -> str:
    raw = f"{SYSTEM_PROMPT}\n{context}"
    # compress if >500 chars
    if len(raw) > 500:
        try:
            comp = call_openai(OPT_KEY, "gpt-3.5-turbo", [
                {"role":"system","content":"Comprimir texto ao máximo mantendo sentido."},
                {"role":"user","content": raw}
            ], temperature=0)
            raw = comp.choices[0].message.content
        except Exception:
            logging.exception("[Compress] failed")

    # try GPT-4o
    try:
        out = call_openai(MAIN_KEY, "gpt-4o", [
            {"role":"system","content":SYSTEM_PROMPT},
            {"role":"user","content": raw}
        ])
        return out.choices[0].message.content.strip()
    except Exception:
        logging.exception("[GPT-4o] failed")

    # fallback Gemini
    for model in GEMINI_MODELS:
        try:
            g = genai.GenerativeModel(model).generate_content([{"role":"user","parts":[raw]}])
            if getattr(g, "text", None):
                return g.text.strip()
        except Exception:
            logging.exception(f"[Gemini:{model}] failed")

    return "Desculpe, falha na geração da resposta. Verifique os logs."

def gerar_resposta_com_memoria(origem: str, msg: str) -> str:
    mem = read_memory()
    hist = [m for m in mem if m["origem"] == origem][-10:]
    ctx  = "\n".join(f"Usuário: {h['entrada']}\nKaizen: {h['resposta']}" for h in hist)
    ctx += f"\nUsuário: {msg}"
    resp = gerar_resposta(ctx)
    write_memory({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "origem":    origem,
        "entrada":   msg,
        "resposta":  resp
    })
    return resp

# ─── TRELLO TASKS ──────────────────────────────────────────────────────────────
def criar_tarefa_trello(title: str, desc: str = "", due_days: int = 1):
    due = (datetime.now(timezone.utc) + timedelta(days=due_days))\
          .replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
    params = {
        "key":   TRELLO_KEY,
        "token": TRELLO_TOKEN,
        "idList":TRELLO_LIST_ID,
        "name":  title[:100],
        "desc":  desc,
        "due":   due,
        "pos":   "top"
    }
    try:
        r = requests.post(f"{TRELLO_API_URL}/cards", params=params)
        r.raise_for_status()
        logging.info(f"[Trello] created card → {title}")
    except Exception:
        logging.exception("[Trello] create card failed")

def parse_card_request(text: str) -> dict:
    m = re.match(r".*criar (?:cart[rã]o|card)(?: chamado)?\s*([^:]+)(?::\s*(.+))?",
                 text, flags=re.IGNORECASE)
    if m:
        return {"title": m.group(1).strip(), "desc": (m.group(2) or "").strip()}
    return {"title": text, "desc": ""}

# ─── PERFORMANCE REPORT ────────────────────────────────────────────────────────
def gerar_relatorio_performance(chat_id: str) -> str:
    mem  = read_memory()
    last = [m for m in mem if m["origem"] == f"tg:{chat_id}"][-5:]
    if not last:
        return "📊 Sem interações registradas."
    lines = [
        f"🕐 {m['timestamp']}\n📥 {m['entrada']}\n📤 {m['resposta']}"
        for m in last
    ]
    return "📊 Últimas interações:\n\n" + "\n\n".join(lines)

# ─── TELEGRAM WEBHOOK ──────────────────────────────────────────────────────────
def enviar_telegram(chat_id: str, text: str):
    try:
        requests.post(TELEGRAM_URL, json={"chat_id": chat_id, "text": text}).raise_for_status()
        logging.info(f"[Telegram] sent → {chat_id}")
    except Exception:
        logging.exception("[Telegram] send failed")

@app.route('/telegram_webhook', methods=['POST'])
def telegram_webhook():
    payload = request.get_json(force=True)
    logging.info(f"[Webhook] payload: {payload}")
    try:
        msg    = payload["message"].get("text", "").strip()
        chatid = str(payload["message"]["chat"]["id"])
        low    = msg.lower()

        if "relatório de performance" in low:
            resp = gerar_relatorio_performance(chatid)

        elif "criar cartão" in low or "criar card" in low:
            card = parse_card_request(msg)
            criar_tarefa_trello(card["title"], card["desc"])
            resp = f"✅ Cartão '{card['title']}' criado."

        elif "criar evento" in low:
            ev    = parse_event_request(msg)
            start = dt_parse(ev["start"])
            end   = dt_parse(ev["end"])
            criar_evento_calendar(ev["title"], start, end)
            resp = f"✅ Evento '{ev['title']}' criado."

        else:
            resp = gerar_resposta_com_memoria(f"tg:{chatid}", msg)

        enviar_telegram(chatid, resp)
    except Exception:
        logging.exception("[Webhook] processing failed")
    return jsonify(ok=True)

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json(force=True)
    msg  = data.get("message", "").strip()
    if not msg:
        return jsonify(error="mensagem vazia"), 400
    low = msg.lower()

    if "criar cartão" in low or "criar card" in low:
        card = parse_card_request(msg)
        criar_tarefa_trello(card["title"], card["desc"])
        return jsonify(status="ok", message=f"Cartão '{card['title']}' criado.")
    if "criar evento" in low:
        ev      = parse_event_request(msg)
        start   = dt_parse(ev["start"])
        end     = dt_parse(ev["end"])
        created = criar_evento_calendar(ev["title"], start, end)
        return jsonify(status="ok", id=created["id"])
    if "relatório de performance" in low:
        return jsonify(reply=gerar_relatorio_performance("webhook"))

    reply = gerar_resposta_com_memoria("webhook", msg)
    return jsonify(reply=reply)

# ─── AUTONOMOUS CYCLES ─────────────────────────────────────────────────────────
def pensar_autonomamente():
    h = datetime.now(CLIENT_TZ).hour
    if   5 <= h < 9:    prompt = "Bom dia. Que atitude tomaria hoje?"
    elif 12 <= h < 14:  prompt = "Hora do almoço. Gere insight produtivo."
    elif 18 <= h < 20:  prompt = "Fim de expediente. O que aprendeu?"
    else:               prompt = "Execute algo útil baseado no histórico."
    try:
        insight = gerar_resposta_com_memoria("saito", prompt)
        enviar_telegram(TELEGRAM_LOOP_ID, insight)
    except Exception:
        logging.exception("[Autonomous] failed")

def heartbeat():
    write_memory({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "origem":    "sistema",
        "entrada":   "heartbeat",
        "resposta":  "ok"
    })

def check_render():
    try:
        st = requests.get("https://kaizen-agente.onrender.com/ask", timeout=5).status_code
        write_memory({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "origem":    "watchdog",
            "entrada":   "check_render",
            "resposta":  f"status {st}"
        })
    except Exception:
        logging.exception("[Watchdog] check_render failed")

def loop(fn, interval):
    while True:
        fn()
        time.sleep(interval)

def loop_relatorio():
    while True:
        now    = datetime.now(CLIENT_TZ)
        target = now.replace(hour=18, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        time.sleep((target - now).total_seconds())
        enviar_telegram(TELEGRAM_LOOP_ID, "🧠 Relatório diário: Kaizen ativo.")

# ─── STARTUP ───────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    threading.Thread(target=loop, args=(heartbeat,     300), daemon=True).start()
    threading.Thread(target=loop, args=(check_render,   600), daemon=True).start()
    threading.Thread(target=loop_relatorio, daemon=True).start()
    threading.Thread(target=loop, args=(pensar_autonomamente, 3600), daemon=True).start()

    port = int(os.environ.get("PORT", "10000"))
    app.run(host='0.0.0.0', port=port)
