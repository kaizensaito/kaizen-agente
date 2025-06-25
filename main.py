import os, re, io, json, time, threading, logging, requests
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify
from dotenv import load_dotenv

import google.generativeai as genai
from openai import OpenAI

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
CLIENT_TZ     = ZoneInfo("America/Sao_Paulo")
MEMORY_LOCK   = threading.Lock()

# ─── ENV VARS ─────────────────────────────────────────────────────────────────
OPT_KEY        = os.getenv("OPENAI_API_KEY_OPTIMIZER")
MAIN_KEY       = os.getenv("OPENAI_API_KEY_MAIN", OPT_KEY)
GEMINI_KEY     = os.getenv("GEMINI_API_KEY")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "{}")

TRELLO_KEY     = os.getenv("TRELLO_KEY")
TRELLO_TOKEN   = os.getenv("TRELLO_TOKEN")
TRELLO_LIST_ID = os.getenv("TRELLO_LIST_ID")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_LOOP_ID = os.getenv("TELEGRAM_CHAT_ID")

logging.info(
    f"KEYS → OPT={'OK' if OPT_KEY else 'MISSING'} "
    f"MAIN={'OK' if MAIN_KEY else 'MISSING'} "
    f"GEMINI={'OK' if GEMINI_KEY else 'MISSING'}"
)

# ─── LLM CLIENTS & USAGE COUNTERS ──────────────────────────────────────────────
opt_client  = OpenAI(api_key=OPT_KEY)
main_client = OpenAI(api_key=MAIN_KEY)
genai.configure(api_key=GEMINI_KEY)

# usage counters to track consumption
usage_counters = {"gpt-3.5-turbo": 0, "gemini": 0}

# only free-tier models
GEMINI_MODELS = ["models/gemini-1.5-flash"]

# initial fallback order
_available_providers = ["gemini", "gpt-3.5-turbo"]
_provider_lock      = threading.Lock()

SYSTEM_PROMPT     = (
    "Você é o Kaizen: assistente autônomo, direto e levemente sarcástico, "
    "que provoca Nilson Saito e impulsiona a melhoria contínua."
)
MAX_CONTEXT_CHARS = 4000

# ─── UTIL: OPENAI & GEMINI CALLS ───────────────────────────────────────────────
def call_openai(client: OpenAI, model: str, messages: list,
                temperature: float = 0.7, max_tokens: int = 1024) -> str:
    # increment usage
    if model in usage_counters:
        usage_counters[model] += 1
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens
    )
    return resp.choices[0].message.content.strip()

def _try_gpt35(raw: str) -> str:
    return call_openai(
        main_client,
        "gpt-3.5-turbo",
        [{"role":"system","content":SYSTEM_PROMPT},
         {"role":"user","content":raw}]
    )

def _try_gemini(raw: str) -> str:
    # count each request
    usage_counters["gemini"] += 1
    for m in GEMINI_MODELS:
        g = genai.GenerativeModel(m).generate_content([{"role":"user","parts":[raw]}])
        text = getattr(g, "text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()
    raise RuntimeError("Gemini sem resposta")

PROVIDERS = {
    "gpt-3.5-turbo": _try_gpt35,
    "gemini":        _try_gemini
}

# ─── CONTEXT MANAGEMENT ────────────────────────────────────────────────────────
def build_context(origem: str, msg: str) -> str:
    mem = read_memory()
    hist = [m for m in mem if m["origem"] == origem]
    blocks, total = [], 0
    for h in reversed(hist):
        blk = f"Usuário: {h['entrada']}\nKaizen: {h['resposta']}\n"
        if total + len(blk) > MAX_CONTEXT_CHARS * 0.8:
            break
        blocks.insert(0, blk)
        total += len(blk)
    blocks.append(f"Usuário: {msg}")
    ctx = SYSTEM_PROMPT + "\n" + "".join(blocks)
    return ctx[-MAX_CONTEXT_CHARS:] if len(ctx) > MAX_CONTEXT_CHARS else ctx

# ─── RESPONSE & MEMORY ────────────────────────────────────────────────────────
def gerar_resposta(raw: str) -> str:
    with _provider_lock:
        order = list(_available_providers)
    errors = {}
    for name in order:
        try:
            logging.info(f"[responder] tentando {name}")
            return PROVIDERS[name](raw)
        except Exception as e:
            logging.warning(f"[responder] {name} falhou: {e}")
            errors[name] = str(e)
    return f"⚠️ Nenhuma IA disponível. Erros: {errors}"

def gerar_resposta_com_memoria(origem: str, msg: str) -> str:
    from datetime import datetime
    ctx  = build_context(origem, msg)
    resp = gerar_resposta(ctx)
    if resp.startswith("⚠️"):
        return resp
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "origem":    origem,
        "entrada":   msg,
        "resposta":  resp
    }
    write_memory(entry)
    return resp

# ─── GOOGLE DRIVE MEMORY ───────────────────────────────────────────────────────
SCOPES          = ['https://www.googleapis.com/auth/drive']
MEMORY_FILENAME = 'kaizen_memory_log.json'
GOOGLE_CREDS    = json.loads(GOOGLE_CREDS_JSON)

def drive_service():
    creds = service_account.Credentials.from_service_account_info(GOOGLE_CREDS, scopes=SCOPES)
    return build_drive('drive', 'v3', credentials=creds, cache_discovery=False)

def get_json_file_id(svc):
    files = svc.files().list(
        q=f"name='{MEMORY_FILENAME}'", spaces='drive', fields='files(id)'
    ).execute().get('files', [])
    if not files:
        raise FileNotFoundError(MEMORY_FILENAME)
    return files[0]['id']

def read_memory() -> list:
    svc = drive_service()
    buf = io.BytesIO()
    dl  = MediaIoBaseDownload(buf, svc.files().get_media(fileId=get_json_file_id(svc)))
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

# ─── TEST & USAGE ROUTES ──────────────────────────────────────────────────────
@app.route('/test_llm', methods=['GET'])
def test_llm():
    report = {}
    # Gemini
    try:
        g = genai.GenerativeModel(GEMINI_MODELS[0]).generate_content([{"role":"user","parts":["Olá"]}])
        report["gemini"] = {"ok": True,  "reply": getattr(g,"text","").strip()}
    except Exception as e:
        report["gemini"] = {"ok": False, "error": str(e)}
    # GPT-3.5
    try:
        r = call_openai(main_client, "gpt-3.5-turbo", [{"role":"user","content":"Olá"}], temperature=0)
        report["gpt-3.5-turbo"] = {"ok": True, "reply": r}
    except Exception as e:
        report["gpt-3.5-turbo"] = {"ok": False, "error": str(e)}
    return jsonify(report)

@app.route('/usage', methods=['GET'])
def usage():
    return jsonify(usage_counters)

# ─── CHAT & TELEGRAM ──────────────────────────────────────────────────────────
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
    data   = request.get_json(force=True)
    msg    = data.get("message",{}).get("text","").strip()
    chatid = str(data.get("message",{}).get("chat",{}).get("id",""))
    if not msg:
        _send_telegram(chatid, "⚠️ Mensagem vazia.")
        return jsonify(ok=True)
    resp = gerar_resposta_com_memoria(f"tg:{chatid}", msg)
    _send_telegram(chatid, resp)
    return jsonify(ok=True)

def _send_telegram(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id":chat_id,"text":text})
        if not r.ok:
            logging.error(f"[telegram] error {r.status_code}: {r.text}")
    except Exception as e:
        logging.exception(f"[telegram] exception: {e}")

# ─── RUN ───────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.getenv("PORT","10000"))
    app.run(host='0.0.0.0', port=port)
