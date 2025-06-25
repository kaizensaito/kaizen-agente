import os, re, io, json, time, threading, logging, requests
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify
from dotenv import load_dotenv

import openai
import google.generativeai as genai

from google.oauth2 import service_account
from googleapiclient.discovery import build as build_drive, build as build_cal
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

# ─── CONFIGURAÇÃO ─────────────────────────────────────────────────────────────
load_dotenv()
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%dT%H:%M:%S%z")

app = Flask(__name__)
CLIENT_TZ = ZoneInfo("America/Sao_Paulo")
MEMORY_LOCK = threading.Lock()

# ─── VARIÁVEIS DE AMBIENTE ────────────────────────────────────────────────────
OPT_KEY    = os.getenv("OPENAI_API_KEY_OPTIMIZER")
MAIN_KEY   = os.getenv("OPENAI_API_KEY_MAIN", OPT_KEY)
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "{}")
TRELLO_KEY = os.getenv("TRELLO_KEY")
TRELLO_TOKEN = os.getenv("TRELLO_TOKEN")
TRELLO_LIST_ID = os.getenv("TRELLO_LIST_ID")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_LOOP_ID = os.getenv("TELEGRAM_CHAT_ID")

logging.info(f"ENV KEYS → OPT:{'OK' if OPT_KEY else 'MISSING'} "
             f"MAIN:{'OK' if MAIN_KEY else 'MISSING'} "
             f"GEMINI:{'OK' if GEMINI_KEY else 'MISSING'}")

# ─── LLM SETUP ─────────────────────────────────────────────────────────────────
openai.api_key = OPT_KEY
genai.configure(api_key=GEMINI_KEY)

GEMINI_MODELS = [
    "models/gemini-1.5-flash",
    "models/gemini-1.5-pro",
    "models/gemini-2.5-pro-preview-06-05"
]

# ordem inicial (pode ajustar manualmente após o /test_llm)
_available_providers = ["gpt-4o", "gpt-3.5-turbo", "gemini"]
_provider_lock = threading.Lock()

SYSTEM_PROMPT = (
    "Você é o Kaizen: assistente autônomo, direto e levemente sarcástico, "
    "que provoca Nilson Saito e impulsiona a melhoria contínua."
)

MAX_CONTEXT_CHARS = 4000

# ─── HELPERS OPENAI / GEMINI ───────────────────────────────────────────────────
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
    raise RuntimeError("Gemini sem resposta de texto")

PROVIDERS = {
    "gpt-4o": _try_gpt4o,
    "gpt-3.5-turbo": _try_gpt35,
    "gemini": _try_gemini
}

# ─── CONTEXT MANAGEMENT ─────────────────────────────────────────────────────────
def build_context(origem: str, msg: str) -> str:
    mem = read_memory()
    hist = [m for m in mem if m["origem"] == origem]
    parts, total = [], 0
    # pega do mais recente para trás até 80% do limite
    for h in reversed(hist):
        blk = f"Usuário: {h['entrada']}\nKaizen: {h['resposta']}\n"
        if total + len(blk) > MAX_CONTEXT_CHARS * 0.8:
            break
        parts.insert(0, blk)
        total += len(blk)
    parts.append(f"Usuário: {msg}")
    ctx = SYSTEM_PROMPT + "\n" + "".join(parts)
    return ctx[-MAX_CONTEXT_CHARS:] if len(ctx) > MAX_CONTEXT_CHARS else ctx

# ─── GERAR RESPOSTA & MEMÓRIA ──────────────────────────────────────────────────
def gerar_resposta(raw: str) -> str:
    with _provider_lock:
        order = list(_available_providers)
    for name in order:
        try:
            logging.info(f"[gerar_resposta] tentando {name}")
            return PROVIDERS[name](raw)
        except Exception:
            logging.exception(f"[gerar_resposta] {name} falhou")
    return "⚠️ Nenhuma IA disponível no momento."

def gerar_resposta_com_memoria(origem: str, msg: str) -> str:
    from datetime import datetime
    ctx  = build_context(origem, msg)
    resp = gerar_resposta(ctx)
    if resp.startswith("⚠️"):
        return resp
    # salva apenas respostas válidas
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "origem":    origem,
        "entrada":   msg,
        "resposta":  resp
    }
    write_memory(entry)
    return resp

# ─── GOOGLE DRIVE MEMORY ───────────────────────────────────────────────────────
SCOPES           = ['https://www.googleapis.com/auth/drive']
MEMORY_FILENAME  = 'kaizen_memory_log.json'
GOOGLE_CREDS     = json.loads(GOOGLE_CREDS_JSON)

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
    done = False
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

# ─── ROTAS DE TESTE & HEALTHCHECK ──────────────────────────────────────────────
@app.route('/', methods=['GET'])
def index():
    return "Kaizen Agent rodando", 200

@app.route('/status', methods=['GET'])
def status():
    return jsonify(ok=True, timestamp=datetime.now(timezone.utc).isoformat())

@app.route('/test_llm', methods=['GET'])
def test_llm():
    report = {}
    # Gemini
    for m in GEMINI_MODELS:
        try:
            g = genai.GenerativeModel(m).generate_content([{"role":"user","parts":["Olá"]}])
            report[m] = {"ok": True, "reply": getattr(g, "text", "").strip()}
            break
        except Exception as e:
            report[m] = {"ok": False, "error": str(e)}
    # GPT-4o
    try:
        r4 = call_openai(MAIN_KEY, "gpt-4o", [{"role":"user","content":"Olá"}], temperature=0)
        report["gpt-4o"] = {"ok": True, "reply": r4.choices[0].message.content.strip()}
    except Exception as e:
        report["gpt-4o"] = {"ok": False, "error": str(e)}
    # GPT-3.5
    try:
        r35 = call_openai(MAIN_KEY, "gpt-3.5-turbo", [{"role":"user","content":"Olá"}], temperature=0)
        report["gpt-3.5-turbo"] = {"ok": True, "reply": r35.choices[0].message.content.strip()}
    except Exception as e:
        report["gpt-3.5-turbo"] = {"ok": False, "error": str(e)}
    return jsonify(report)

# ─── ROTAS DE CHAT & TELEGRAM ──────────────────────────────────────────────────
@app.route('/ask', methods=['GET'])
def ask_get():
    return "Use POST /ask com JSON {\"message\":\"...\"}", 200

@app.route('/ask', methods=['POST'])
def ask_post():
    data = request.get_json(force=True)
    msg  = data.get("message","").strip()
    if not msg:
        return jsonify(error="mensagem vazia"), 400
    reply = gerar_resposta_com_memoria("web", msg)
    return jsonify(reply=reply)

@app.route('/telegram_webhook', methods=['POST'])
def telegram_webhook():
    payload = request.get_json(force=True)
    msg     = payload.get("message",{}).get("text","").strip()
    chat_id = str(payload.get("message",{}).get("chat",{}).get("id",""))
    if not msg:
        _send_telegram(chat_id, "⚠️ Mensagem vazia recebida.")
        return jsonify(ok=True)
    low = msg.lower()
    # aqui você pode botar regex para criar evento/cartão...
    resp = gerar_resposta_com_memoria(f"tg:{chat_id}", msg)
    _send_telegram(chat_id, resp)
    return jsonify(ok=True)

def _send_telegram(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id":chat_id,"text":text})

# ─── STARTUP ───────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.getenv("PORT","10000"))
    app.run(host='0.0.0.0', port=port)
