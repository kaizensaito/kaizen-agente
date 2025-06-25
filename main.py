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

# ─── CONFIG & LOGGING ─────────────────────────────────────────────────────────
load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z"
)
app = Flask(__name__)
CLIENT_TZ   = ZoneInfo("America/Sao_Paulo")
MEMORY_LOCK = threading.Lock()

# ─── ENV VAR CHECK ────────────────────────────────────────────────────────────
OPT_KEY = os.getenv("OPENAI_API_KEY_OPTIMIZER")
MAIN_KEY = os.getenv("OPENAI_API_KEY_MAIN", OPT_KEY)
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
logging.info(f"ENV: OPT_KEY={'set' if OPT_KEY else 'MISSING'} "
             f"MAIN_KEY={'set' if MAIN_KEY else 'MISSING'} "
             f"GEMINI_KEY={'set' if GEMINI_KEY else 'MISSING'}")

# ─── LLM SETUP ─────────────────────────────────────────────────────────────────
openai.api_key = OPT_KEY  # chave padrão
genai.configure(api_key=GEMINI_KEY)

GEMINI_MODELS = [
    "models/gemini-1.5-flash",
    "models/gemini-1.5-pro",
    "models/gemini-2.5-pro-preview-06-05"
]

SYSTEM_PROMPT = (
    "Você é o Kaizen: um assistente autônomo, direto e levemente sarcástico, "
    "que provoca Nilson Saito e o impulsiona à melhoria contínua. "
    "Desafie suposições, ofereça insights práticos e não tenha medo de cutucar o conformismo."
)

# ─── HEALTH & TEST ENDPOINTS ──────────────────────────────────────────────────
@app.route('/', methods=['GET'])
def index():
    return "Kaizen Agent is running", 200

@app.route('/ask', methods=['GET'])
def ask_get():
    return "Use POST /ask com JSON {message: '...'}", 200

@app.route('/test_llm', methods=['GET'])
def test_llm():
    report = {}
    payload = {"model":"gpt-3.5-turbo","messages":[{"role":"user","content":"Teste Kaizen"}]}
    # 1) teste Gemini
    for m in GEMINI_MODELS:
        try:
            logging.info(f"[test_llm] Gemini {m}")
            g = genai.GenerativeModel(m).generate_content([{"role":"user","parts":["Olá"]}])
            report[m] = {"ok": True, "reply": getattr(g, "text", "")}
            break
        except Exception as e:
            report[m] = {"ok": False, "error": str(e)}
    # 2) teste GPT-4o
    try:
        logging.info("[test_llm] GPT-4o")
        resp4 = call_openai(MAIN_KEY, "gpt-4o", [
            {"role":"user","content":"Olá"}
        ], temperature=0)
        report["gpt-4o"] = {"ok": True, "reply": resp4.choices[0].message.content.strip()}
    except Exception as e:
        report["gpt-4o"] = {"ok": False, "error": str(e)}
    # 3) teste GPT-3.5
    try:
        logging.info("[test_llm] gpt-3.5-turbo")
        resp35 = call_openai(MAIN_KEY, "gpt-3.5-turbo", [
            {"role":"user","content":"Olá"}
        ], temperature=0)
        report["gpt-3.5-turbo"] = {"ok": True, "reply": resp35.choices[0].message.content.strip()}
    except Exception as e:
        report["gpt-3.5-turbo"] = {"ok": False, "error": str(e)}
    return jsonify(report)

# ─── CORE LLM HELPER ──────────────────────────────────────────────────────────
def call_openai(key, model, messages, temperature=0.7):
    prev = openai.api_key
    openai.api_key = key
    resp = openai.ChatCompletion.create(
        model=model, messages=messages, temperature=temperature
    )
    openai.api_key = prev
    return resp

def gerar_resposta(context: str) -> str:
    raw = f"{SYSTEM_PROMPT}\n{context}"

    # 1) Gemini first
    for m in GEMINI_MODELS:
        try:
            logging.info(f"[gerar_resposta] tentando Gemini {m}")
            g = genai.GenerativeModel(m).generate_content([{"role":"user","parts":[raw]}])
            if getattr(g, "text", None):
                return g.text.strip()
        except Exception as e:
            logging.exception(f"[Gemini:{m}] falhou")

    # 2) GPT-4o
    try:
        logging.info("[gerar_resposta] tentando GPT-4o")
        out4 = call_openai(MAIN_KEY, "gpt-4o", [
            {"role":"system","content":SYSTEM_PROMPT},
            {"role":"user","content": raw}
        ], temperature=0.7)
        return out4.choices[0].message.content.strip()
    except Exception:
        logging.exception("[GPT-4o] falhou")

    # 3) GPT-3.5 fallback
    try:
        logging.info("[gerar_resposta] tentando gpt-3.5-turbo")
        out35 = call_openai(MAIN_KEY, "gpt-3.5-turbo", [
            {"role":"system","content":SYSTEM_PROMPT},
            {"role":"user","content": raw}
        ], temperature=0.7)
        return out35.choices[0].message.content.strip()
    except Exception:
        logging.exception("[gpt-3.5-turbo] falhou")

    # 4) ultimate fallback
    return "✖️ Todos LLMs falharam. Confira os logs para detalhes."

# ─── MEMORY (Google Drive JSON) ───────────────────────────────────────────────
SCOPES         = ['https://www.googleapis.com/auth/drive']
JSON_FILENAME  = 'kaizen_memory_log.json'
GOOGLE_CREDS   = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])

def drive_service():
    creds = service_account.Credentials.from_service_account_info(GOOGLE_CREDS, scopes=SCOPES)
    return build_drive('drive', 'v3', credentials=creds, cache_discovery=False)

def get_json_file_id(svc):
    files = svc.files().list(q=f"name='{JSON_FILENAME}'", spaces='drive', fields='files(id)').execute().get('files', [])
    if not files:
        raise FileNotFoundError(JSON_FILENAME)
    return files[0]['id']

def read_memory():
    svc = drive_service()
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, svc.files().get_media(fileId=get_json_file_id(svc)))
    done=False
    while not done:
        _, done = dl.next_chunk()
    buf.seek(0)
    return json.load(buf)

def write_memory(entry: dict):
    with MEMORY_LOCK:
        svc = drive_service()
        fid = get_json_file_id(svc)
        mem = read_memory()
        mem.append(entry)
        buf = io.BytesIO(json.dumps(mem, indent=2).encode('utf-8'))
        svc.files().update(fileId=fid, media_body=MediaIoBaseUpload(buf, mimetype='application/json')).execute()

# ─── CALENDAR PARSER & CREATION ────────────────────────────────────────────────
GOOGLE_CAL_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
def parse_event_request(text: str) -> dict:
    prev, openai.api_key = openai.api_key, OPT_KEY
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-3.5-turbo", temperature=0,
            messages=[
                {"role":"system","content":
                 "Parser JSON de evento: {'title':...,'start':...,'end':...'} em ISO8601."},
                {"role":"user","content": text}
            ]
        )
        return json.loads(resp.choices[0].message.content)
    finally:
        openai.api_key = prev

def criar_evento_calendar(title, start, end):
    svc = calendar_service()
    ev = svc.events().insert(
        calendarId=GOOGLE_CAL_ID,
        body={
            'summary':title,
            'start':  {'dateTime':start.isoformat(),'timeZone':'America/Sao_Paulo'},
            'end':    {'dateTime':end.isoformat(),'timeZone':'America/Sao_Paulo'}
        }
    ).execute()
    logging.info(f"[Calendar] evento criado {ev['id']} – {title}")
    return ev

def calendar_service():
    creds = service_account.Credentials.from_service_account_info(
        GOOGLE_CREDS, scopes=['https://www.googleapis.com/auth/calendar']
    )
    return build_cal('calendar','v3', credentials=creds, cache_discovery=False)

# ─── TRELLO ───────────────────────────────────────────────────────────────────
TRELLO_KEY     = os.getenv("TRELLO_KEY")
TRELLO_TOKEN   = os.getenv("TRELLO_TOKEN")
TRELLO_LIST_ID = os.getenv("TRELLO_LIST_ID")
TRELLO_URL     = "https://api.trello.com/1"

def criar_tarefa_trello(title, desc="", due_days=1):
    due = (datetime.now(timezone.utc)+timedelta(days=due_days))\
          .replace(hour=9,minute=0,second=0,microsecond=0).isoformat()
    try:
        requests.post(
            f"{TRELLO_URL}/cards",
            params={
                "key":TRELLO_KEY,"token":TRELLO_TOKEN,
                "idList":TRELLO_LIST_ID,"name":title,"desc":desc,"due":due
            }
        ).raise_for_status()
        logging.info(f"[Trello] card criado → {title}")
    except Exception:
        logging.exception("[Trello] falha ao criar card")

def parse_card_request(text: str):
    m = re.match(r".*criar (?:cart[rã]o|card)(?: chamado)?\s*([^:]+)(?::\s*(.+))?", text, flags=re.IGNORECASE)
    return {"title":m.group(1).strip(),"desc":(m.group(2)or"").strip()} if m else {"title":text,"desc":""}

# ─── TELEGRAM ─────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_LOOP_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_URL     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

def enviar_telegram(chat_id, text):
    try:
        requests.post(TELEGRAM_URL, json={"chat_id":chat_id,"text":text}).raise_for_status()
        logging.info(f"[Telegram] enviado → {chat_id}")
    except Exception:
        logging.exception("[Telegram] falha ao enviar")

@app.route('/telegram_webhook', methods=['POST'])
def telegram_webhook():
    data = request.get_json(force=True)
    logging.info(f"[Webhook] payload: {data}")
    try:
        msg    = data["message"].get("text","").strip()
        chatid = str(data["message"]["chat"]["id"])
        low    = msg.lower()

        if "relatório de performance" in low:
            resp = gerar_relatorio_performance(chatid)
        elif "criar cartão" in low or "criar card" in low:
            card = parse_card_request(msg); criar_tarefa_trello(card["title"], card["desc"])
            resp = f"✅ Cartão '{card['title']}' criado."
        elif "criar evento" in low:
            ev    = parse_event_request(msg)
            start, end = dt_parse(ev["start"]), dt_parse(ev["end"])
            criar_evento_calendar(ev["title"], start, end)
            resp = f"✅ Evento '{ev['title']}' criado."
        else:
            resp = gerar_resposta_com_memoria(f"tg:{chatid}", msg)

        enviar_telegram(chatid, resp)
    except Exception:
        logging.exception("[Webhook] falha no processamento")
    return jsonify(ok=True)

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json(force=True)
    msg  = data.get("message","").strip()
    if not msg:
        return jsonify(error="mensagem vazia"),400
    low = msg.lower()

    if "criar cartão" in low or "criar card" in low:
        card = parse_card_request(msg); criar_tarefa_trello(card["title"], card["desc"])
        return jsonify(status="ok",message=f"Cartão '{card['title']}' criado.")
    if "criar evento" in low:
        ev         = parse_event_request(msg)
        start,end = dt_parse(ev["start"]), dt_parse(ev["end"])
        created    = criar_evento_calendar(ev["title"], start, end)
        return jsonify(status="ok",id=created["id"])
    if "relatório de performance" in low:
        return jsonify(reply=gerar_relatorio_performance("webhook"))

    reply = gerar_resposta_com_memoria("webhook", msg)
    return jsonify(reply=reply)

# ─── PERFORMANCE REPORT ────────────────────────────────────────────────────────
def gerar_relatorio_performance(chat_id):
    mem  = read_memory()
    last = [m for m in mem if m["origem"]==f"tg:{chat_id}"][-5:]
    if not last:
        return "📊 Sem interações registradas."
    lines = [f"🕐 {m['timestamp']}\n📥 {m['entrada']}\n📤 {m['resposta']}" for m in last]
    return "📊 Últimas interações:\n\n" + "\n\n".join(lines)

# ─── THREADS & CICLOS ─────────────────────────────────────────────────────────
def loop(fn, interval):
    while True:
        fn()
        time.sleep(interval)

def heartbeat():
    write_memory({"timestamp":datetime.now(timezone.utc).isoformat(),
                  "origem":"sistema","entrada":"heartbeat","resposta":"ok"})

def check_render():
    try:
        st = requests.get("https://kaizen-agente.onrender.com/ask", timeout=5).status_code
        write_memory({"timestamp":datetime.now(timezone.utc).isoformat(),
                      "origem":"watchdog","entrada":"check_render","resposta":f"status {st}"})
    except Exception:
        logging.exception("[Watchdog] failed")

def pensar_autonomamente():
    h = datetime.now(CLIENT_TZ).hour
    if 5<=h<9:    prompt="Bom dia. Que atitude tomaria hoje?"
    elif 12<=h<14:prompt="Hora do almoço. Gere insight produtivo."
    elif 18<=h<20:prompt="Fim de expediente. O que aprendeu?"
    else:         prompt="Execute algo útil baseado no histórico."
    try:
        insight = gerar_resposta_com_memoria("saito", prompt)
        enviar_telegram(TELEGRAM_LOOP_ID, insight)
    except Exception:
        logging.exception("[Autonomous] failed")

if __name__ == '__main__':
    # start threads
    threading.Thread(target=loop, args=(heartbeat,300), daemon=True).start()
    threading.Thread(target=loop, args=(check_render,600), daemon=True).start()
    threading.Thread(target=loop, args=(pensar_autonomamente,3600), daemon=True).start()

    port = int(os.environ.get("PORT", "10000"))
    app.run(host='0.0.0.0', port=port)
