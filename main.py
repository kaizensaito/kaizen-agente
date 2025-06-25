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
CLIENT_TZ = ZoneInfo("America/Sao_Paulo")
MEMORY_LOCK = threading.Lock()

# ─── ENV VARS ─────────────────────────────────────────────────────────────────
OPT_KEY      = os.getenv("OPENAI_API_KEY_OPTIMIZER")
MAIN_KEY     = os.getenv("OPENAI_API_KEY_MAIN", OPT_KEY)
GEMINI_KEY   = os.getenv("GEMINI_API_KEY")
GOOGLE_CREDS = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON", "{}"))

TRELLO_KEY   = os.getenv("TRELLO_KEY")
TRELLO_TOKEN = os.getenv("TRELLO_TOKEN")
TRELLO_LIST_ID = os.getenv("TRELLO_LIST_ID")

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_LOOP_ID = os.getenv("TELEGRAM_CHAT_ID")

logging.info(f"Keys: OPT={'OK' if OPT_KEY else 'MISSING'} "
             f"MAIN={'OK' if MAIN_KEY else 'MISSING'} "
             f"GEMINI={'OK' if GEMINI_KEY else 'MISSING'}")

# ─── LLM & MODEL LISTS ────────────────────────────────────────────────────────
openai.api_key = OPT_KEY
genai.configure(api_key=GEMINI_KEY)

GEMINI_MODELS = [
    "models/gemini-1.5-flash",
    "models/gemini-1.5-pro",
    "models/gemini-2.5-pro-preview-06-05"
]

# initial provider order; will be updated dynamically
_provider_lock = threading.Lock()
_available_providers = ["gpt-4o", "gpt-3.5-turbo", "gemini"]

SYSTEM_PROMPT = (
    "Você é o Kaizen: um assistente autônomo, direto e levemente sarcástico, "
    "que provoca Nilson Saito e impulsiona a melhoria contínua."
)

# ─── UTIL: CALL OPENAI ─────────────────────────────────────────────────────────
def call_openai(key, model, messages, temperature=0.7, max_tokens=1024):
    prev = openai.api_key
    openai.api_key = key
    resp = openai.ChatCompletion.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens
    )
    openai.api_key = prev
    return resp

# ─── PROVIDERS ────────────────────────────────────────────────────────────────
def _try_gpt4o(raw: str) -> str:
    out = call_openai(MAIN_KEY, "gpt-4o", [
        {"role":"system","content":SYSTEM_PROMPT},
        {"role":"user","content":raw}
    ])
    return out.choices[0].message.content.strip()

def _try_gpt35(raw: str) -> str:
    out = call_openai(MAIN_KEY, "gpt-3.5-turbo", [
        {"role":"system","content":SYSTEM_PROMPT},
        {"role":"user","content":raw}
    ])
    return out.choices[0].message.content.strip()

def _try_gemini(raw: str) -> str:
    for m in GEMINI_MODELS:
        g = genai.GenerativeModel(m).generate_content([{"role":"user","parts":[raw]}])
        text = getattr(g, "text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()
    raise RuntimeError("Gemini returned no text")

_PROVIDER_FUNCS = {
    "gpt-4o":    _try_gpt4o,
    "gpt-3.5-turbo": _try_gpt35,
    "gemini":    _try_gemini
}

# ─── CONTEXT MANAGEMENT ────────────────────────────────────────────────────────
MAX_CONTEXT_CHARS = 4000

def build_context(origem: str, msg: str) -> str:
    mem = read_memory()
    history = [m for m in mem if m["origem"] == origem]
    parts = []
    total = 0
    # reverse history to take newest first
    for h in reversed(history):
        block = f"Usuário: {h['entrada']}\nKaizen: {h['resposta']}\n"
        if total + len(block) > MAX_CONTEXT_CHARS * 0.8:
            break
        parts.insert(0, block)
        total += len(block)
    parts.append(f"Usuário: {msg}")
    context = SYSTEM_PROMPT + "\n" + "".join(parts)
    # ensure not above limit
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[-MAX_CONTEXT_CHARS:]
    return context

# ─── DYNAMIC FALLBACK HEALTHCHECK ─────────────────────────────────────────────
HEALTH_INTERVAL = 10 * 60  # 10 minutes
TEST_PROMPT = "Teste de saúde do Kaizen"

def healthcheck_loop():
    global _available_providers
    while True:
        new_list = []
        # check each provider
        for name in ["gpt-4o", "gpt-3.5-turbo", "gemini"]:
            try:
                _PROVIDER_FUNCS[name](TEST_PROMPT)
                new_list.append(name)
                logging.info(f"[health] {name} OK")
            except Exception as e:
                logging.warning(f"[health] {name} FAIL: {e}")
        with _provider_lock:
            _available_providers = new_list or _available_providers
        time.sleep(HEALTH_INTERVAL)

threading.Thread(target=healthcheck_loop, daemon=True).start()

# ─── RESPONSE GENERATION ──────────────────────────────────────────────────────
def gerar_resposta(raw: str) -> str:
    with _provider_lock:
        order = list(_available_providers)
    for name in order:
        try:
            logging.info(f"[gerar_resposta] tentando {name}")
            resp = _PROVIDER_FUNCS[name](raw)
            return resp
        except Exception:
            logging.exception(f"[gerar_resposta] {name} falhou")
    return "⚠️ Nenhuma IA disponível no momento."

def gerar_resposta_com_memoria(origem: str, msg: str) -> str:
    from datetime import datetime, timezone
    ctx = build_context(origem, msg)
    try:
        resp = gerar_resposta(ctx)
        if resp.startswith("⚠️"):
            return resp
        # save to memory
        write_memory({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "origem":    origem,
            "entrada":   msg,
            "resposta":  resp
        })
        return resp
    except Exception:
        logging.exception("[memória] falha")
        return "⚠️ Erro interno. Tente novamente."

# ─── GOOGLE DRIVE MEMORY ───────────────────────────────────────────────────────
SCOPES          = ['https://www.googleapis.com/auth/drive']
MEMORY_FILENAME = 'kaizen_memory_log.json'

def drive_service():
    creds = service_account.Credentials.from_service_account_info(GOOGLE_CREDS, scopes=SCOPES)
    return build_drive('drive', 'v3', credentials=creds, cache_discovery=False)

def get_json_file_id(svc):
    files = svc.files().list(q=f"name='{MEMORY_FILENAME}'", spaces='drive', fields='files(id)').execute().get('files',[])
    if not files:
        raise FileNotFoundError(MEMORY_FILENAME)
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
        buf = io.BytesIO(json.dumps(mem, indent=2).encode())
        svc.files().update(fileId=fid, media_body=MediaIoBaseUpload(buf, 'application/json')).execute()

# ─── FLASK ROUTES ─────────────────────────────────────────────────────────────
@app.route('/', methods=['GET'])
def index():
    return "Kaizen Agent is running", 200

@app.route('/status', methods=['GET'])
def status():
    return jsonify(ok=True, timestamp=datetime.now(timezone.utc).isoformat())

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json(force=True)
    msg  = data.get("message","").strip()
    if not msg:
        return jsonify(error="mensagem vazia"), 400
    reply = gerar_resposta_com_memoria("web", msg)
    return jsonify(reply=reply)

@app.route('/telegram_webhook', methods=['POST'])
def telegram_webhook():
    data = request.get_json(force=True)
    msg = data.get("message",{}).get("text","").strip()
    chatid = str(data.get("message",{}).get("chat",{}).get("id",""))
    if not msg:
        enviar_telegram(chatid, "⚠️ Mensagem vazia.")
        return jsonify(ok=True)
    low = msg.lower()
    try:
        if "performance" in low:
            resp = gerar_relatorio_performance(chatid)
        elif re.search(r"\b(?:criar|marcar|agendar)\s+(?:evento|compromisso|reuni(?:ão|ao))", low):
            ev = parse_event_request(msg)
            start,end = dt_parse(ev["start"]), dt_parse(ev["end"])
            criar_evento_calendar(ev["title"], start, end)
            resp = f"✅ Evento '{ev['title']}' criado."
        elif "cartão" in low or "card" in low:
            card = parse_card_request(msg)
            criar_tarefa_trello(card["title"], card["desc"])
            resp = f"✅ Cartão '{card['title']}' criado."
        else:
            resp = gerar_resposta_com_memoria(f"tg:{chatid}", msg)
        enviar_telegram(chatid, resp)
    except Exception:
        logging.exception("[webhook] erro")
    return jsonify(ok=True)

# ─── AUX: Trello, Calendar, Performance ───────────────────────────────────────
def enviar_telegram(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id":chat_id,"text":text})

def parse_card_request(text):
    m = re.match(r".*criar (?:cart[rã]o|card)(?: chamado)?\s*([^:]+)(?::\s*(.+))?", text, re.IGNORECASE)
    return {"title":m.group(1).strip(),"desc":(m.group(2)or"").strip()} if m else {"title":text,"desc":""}

def criar_tarefa_trello(title, desc=""):
    due = (datetime.now(timezone.utc)+timedelta(days=1))\
        .replace(hour=9,minute=0,second=0,microsecond=0).isoformat()
    params = {"key":TRELLO_KEY,"token":TRELLO_TOKEN,"idList":TRELLO_LIST_ID,
              "name":title,"desc":desc,"due":due}
    requests.post("https://api.trello.com/1/cards", params=params)

GOOGLE_CAL_ID = os.getenv("GOOGLE_CALENDAR_ID","primary")
def calendar_service():
    creds = service_account.Credentials.from_service_account_info(GOOGLE_CREDS,
        scopes=['https://www.googleapis.com/auth/calendar'])
    return build_cal('calendar','v3',credentials=creds,cache_discovery=False)

def parse_event_request(text):
    prev = openai.api_key; openai.api_key = OPT_KEY
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-3.5-turbo", temperature=0,
            messages=[
                {"role":"system","content":
                 "Parser JSON de evento: {'title':...,'start':...,'end':...'} em ISO8601."},
                {"role":"user","content":text}
            ]
        )
        return json.loads(resp.choices[0].message.content)
    finally:
        openai.api_key = prev

def criar_evento_calendar(title, start, end):
    calendar_service().events().insert(
        calendarId=GOOGLE_CAL_ID,
        body={'summary':title,
              'start':{'dateTime':start.isoformat(),'timeZone':'America/Sao_Paulo'},
              'end':  {'dateTime':end.isoformat(),  'timeZone':'America/Sao_Paulo'}}
    ).execute()

def gerar_relatorio_performance(chat_id):
    mem = read_memory()
    last = [m for m in mem if m["origem"]==f"tg:{chat_id}"][-5:]
    if not last: return "📊 Sem interações."
    return "📊 Últimas:\n\n" + "\n\n".join(
        f"🕐 {m['timestamp']}\n📥 {m['entrada']}\n📤 {m['resposta']}" for m in last
    )

# ─── RUN ───────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.getenv("PORT","10000"))
    app.run(host='0.0.0.0', port=port)
