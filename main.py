import os, io, json, time, threading, logging, requests
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify
from dotenv import load_dotenv

import google.generativeai as genai
from openai import OpenAI
from google.oauth2 import service_account
from googleapiclient.discovery import build as build_drive
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

# ─── CONFIG & LOGGING ─────────────────────────────────────────────────────────
load_dotenv()
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")

app = Flask(__name__)
CLIENT_TZ    = ZoneInfo("America/Sao_Paulo")
MEMORY_LOCK  = threading.Lock()

# ─── ENVIRONMENT ──────────────────────────────────────────────────────────────
OPT_KEY          = os.getenv("OPENAI_API_KEY_OPTIMIZER")
MAIN_KEY         = os.getenv("OPENAI_API_KEY_MAIN", OPT_KEY)
GEMINI_KEY       = os.getenv("GEMINI_API_KEY")
HF_TOKEN         = os.getenv("HUGGINGFACE_API_TOKEN")
OR_KEY           = os.getenv("OPENROUTER_API_KEY")
GOOGLE_CREDS     = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON", "{}"))
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

logging.info(f"Env: OPT={'OK' if OPT_KEY else 'MISSING'} "
             f"MAIN={'OK' if MAIN_KEY else 'MISSING'} "
             f"GEMINI={'OK' if GEMINI_KEY else 'MISSING'} "
             f"HF={'OK' if HF_TOKEN else 'MISSING'} "
             f"OR={'OK' if OR_KEY else 'MISSING'} "
             f"TG_TOKEN={'OK' if TELEGRAM_TOKEN else 'MISSING'} "
             f"TG_CHAT_ID={'OK' if TELEGRAM_CHAT_ID else 'MISSING'}")

# ─── LLM CLIENTS & PROMPT ─────────────────────────────────────────────────────
opt_client  = OpenAI(api_key=OPT_KEY)
main_client = OpenAI(api_key=MAIN_KEY)
genai.configure(api_key=GEMINI_KEY)

SYSTEM_PROMPT     = ("Você é o Kaizen: assistente autônomo, direto e levemente "
                     "sar​cástico, que provoca Nilson Saito e impulsiona a "
                     "melhoria contínua.")
MAX_CONTEXT_CHARS = 4000

# ─── PROVIDERS, USAGE & LIMITS ────────────────────────────────────────────────
PROVIDERS = {
    "gpt-3.5-turbo": lambda raw: main_client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role":"system","content":SYSTEM_PROMPT},
                  {"role":"user",  "content":raw}]
    ).choices[0].message.content.strip(),

    "gemini": lambda raw: genai.GenerativeModel("models/gemini-1.5-flash")
        .generate_content([{"role":"user","parts":[raw]}]).text.strip(),

    "mistral": lambda raw: requests.post(
        "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.1",
        headers={"Authorization":f"Bearer {HF_TOKEN}"},
        json={"inputs":raw}, timeout=30
    ).json()[0]["generated_text"].strip(),

    "openrouter": lambda raw: requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization":f"Bearer {OR_KEY}",
            "HTTP-Referer": "https://kaizen-agent",
            "X-Title":      "Kaizen Agent"
        },
        json={
            "model":"mistral",
            "messages":[
                {"role":"system","content":SYSTEM_PROMPT},
                {"role":"user",  "content":raw}
            ]
        }, timeout=30
    ).json()["choices"][0]["message"]["content"].strip()
}

FALLBACK_ORDER = list(PROVIDERS.keys())
usage_counters = {name: 0 for name in FALLBACK_ORDER}
DAILY_LIMITS   = {"gemini": 50, "gpt-3.5-turbo": 1000}
CACHE          = {}
_lock          = threading.Lock()

def _is_within_limit(name: str) -> bool:
    limit = DAILY_LIMITS.get(name)
    return True if limit is None else usage_counters[name] < limit

def cached_try(name: str, fn, raw: str) -> str:
    key = (name, raw)
    if key in CACHE:
        return CACHE[key]
    resp = fn(raw)
    CACHE[key] = resp
    return resp

# ─── CONTEXT & RESPONSE ───────────────────────────────────────────────────────
def build_context(origem: str, msg: str) -> str:
    mem = read_memory()
    hist = [m for m in mem if m["origem"] == origem]
    parts, total = [], 0
    for h in reversed(hist):
        blk = f"Usuário: {h['entrada']}\nKaizen: {h['resposta']}\n"
        if total + len(blk) > MAX_CONTEXT_CHARS*0.8: break
        parts.insert(0, blk); total += len(blk)
    parts.append(f"Usuário: {msg}")
    ctx = SYSTEM_PROMPT + "\n" + "".join(parts)
    return ctx[-MAX_CONTEXT_CHARS:] if len(ctx)>MAX_CONTEXT_CHARS else ctx

def gerar_resposta(raw: str) -> str:
    errors = {}
    with _lock:
        order = FALLBACK_ORDER.copy()
    for name in order:
        if not _is_within_limit(name):
            errors[name] = "quota excedida"
            continue
        try:
            logging.info(f"[responder] tentando {name}")
            usage_counters[name] += 1
            return cached_try(name, PROVIDERS[name], raw)
        except Exception as e:
            logging.warning(f"[{name}] falhou: {e}")
            errors[name] = str(e)
    return f"⚠️ Nenhuma IA disponível. Erros: {errors}"

def gerar_resposta_com_memoria(origem: str, msg: str) -> str:
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
MEMORY_FILE     = 'kaizen_memory_log.json'

def drive_service():
    creds = service_account.Credentials.from_service_account_info(
        GOOGLE_CREDS, scopes=SCOPES)
    return build_drive('drive','v3',credentials=creds,cache_discovery=False)

def get_json_file_id(svc):
    files = svc.files().list(
        q=f"name='{MEMORY_FILE}'", spaces='drive', fields='files(id)'
    ).execute().get('files', [])
    if not files: raise FileNotFoundError(MEMORY_FILE)
    return files[0]['id']

def read_memory() -> list:
    svc = drive_service(); buf = io.BytesIO(); done=False
    dl  = MediaIoBaseDownload(buf, svc.files().get_media(
           fileId=get_json_file_id(svc)))
    while not done: _, done = dl.next_chunk()
    buf.seek(0); return json.load(buf)

def write_memory(entry: dict):
    with MEMORY_LOCK:
        svc = drive_service(); fid = get_json_file_id(svc)
        mem = read_memory(); mem.append(entry)
        buf = io.BytesIO(json.dumps(mem,indent=2).encode())
        svc.files().update(fileId=fid, media_body=MediaIoBaseUpload(
            buf,'application/json')).execute()

# ─── FLASK ROUTES & TELEGRAM ─────────────────────────────────────────────────
@app.route('/', methods=['GET'])
def index(): return "Kaizen Agent is running", 200

@app.route('/status', methods=['GET'])
def status(): return jsonify(ok=True,
                              timestamp=datetime.now(timezone.utc).isoformat())

@app.route('/usage', methods=['GET'])
def usage(): return jsonify(usage_counters)

@app.route('/test_llm', methods=['GET'])
def test_llm():
    out = {}
    for name in FALLBACK_ORDER:
        try:
            snippet = "Olá Kaizen"
            reply   = cached_try(name, PROVIDERS[name], snippet)
            out[name] = {"ok":True, "reply":reply}
        except Exception as e:
            out[name] = {"ok":False,"error":str(e)}
    return jsonify(out)

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json(force=True)
    msg  = data.get("message","").strip()
    if not msg: return jsonify(error="mensagem vazia"), 400
    return jsonify(reply=gerar_resposta_com_memoria("web",msg))

@app.route('/telegram_webhook', methods=['POST'])
def telegram_webhook():
    payload = request.get_json(force=True)
    msg     = payload.get("message",{}).get("text","").strip()
    chat_id = str(payload.get("message",{}).get("chat",{}).get("id",""))
    if not msg:
        _send_telegram(chat_id, "⚠️ Mensagem vazia.")
        return jsonify(ok=True)
    resp = gerar_resposta_com_memoria(f"tg:{chat_id}", msg)
    _send_telegram(chat_id, resp)
    return jsonify(ok=True)

def _send_telegram(chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id":chat_id, "text":text})
        if not r.ok:
            logging.error(f"[telegram] {r.status_code}: {r.text}")
    except Exception:
        logging.exception("[telegram] exception")

# ─── AUTONOMOUS LOOP (a cada 4h) ─────────────────────────────────────────────
def pensar_autonomamente():
    try:
        prompt  = "Gere um insight produtivo."
        insight = gerar_resposta_com_memoria("saito", prompt)
        _send_telegram(TELEGRAM_CHAT_ID, insight)
    except Exception:
        logging.exception("[autonomous] falhou")

def loop_pensar():
    while True:
        pensar_autonomamente()
        time.sleep(4 * 3600)

threading.Thread(target=loop_pensar, daemon=True).start()

# ─── RESET DIÁRIO DAS COTAS ───────────────────────────────────────────────────
def reset_daily_counters():
    while True:
        now     = datetime.now(timezone.utc)
        nxt_mid = (now + timedelta(days=1)).replace(
            hour=0,minute=0,second=0,microsecond=0)
        time.sleep((nxt_mid - now).total_seconds())
        for k in usage_counters: usage_counters[k] = 0
        logging.info("[quota] contadores resetados")

threading.Thread(target=reset_daily_counters, daemon=True).start()

# ─── EXECUTION ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.getenv("PORT","10000"))
    app.run(host='0.0.0.0', port=port)
