import os
import io
import json
import time
import threading
import logging
import requests

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
CLIENT_TZ   = ZoneInfo("America/Sao_Paulo")
MEMORY_LOCK = threading.Lock()

# ─── ENV VARS ─────────────────────────────────────────────────────────────────
OPENAI_KEY       = os.getenv("OPENAI_API_KEY_MAIN")
GEMINI_KEY       = os.getenv("GEMINI_API_KEY")
HF_TOKEN         = os.getenv("HUGGINGFACE_API_TOKEN")
OR_KEY           = os.getenv("OPENROUTER_API_KEY")
GOOGLE_CREDS     = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON", "{}"))
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

logging.info(
    f"OPENAI={'OK' if OPENAI_KEY else 'MISSING'} • "
    f"GEMINI={'OK' if GEMINI_KEY else 'MISSING'} • "
    f"HF={'OK' if HF_TOKEN else 'MISSING'} • "
    f"OR={'OK' if OR_KEY else 'MISSING'} • "
    f"TG_TOKEN={'OK' if TELEGRAM_TOKEN else 'MISSING'} • "
    f"TG_CHAT_ID={'OK' if TELEGRAM_CHAT_ID else 'MISSING'}"
)

# ─── LLM CLIENTS & SYSTEM PROMPT ───────────────────────────────────────────────
openai_client = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)

SYSTEM_PROMPT = (
    "Você é o Kaizen: assistente autônomo, direto e levemente sarcástico, "
    "que provoca Nilson Saito e impulsiona a melhoria contínua."
)
MAX_CTX = 4000

# ─── PROVIDER CALLS ────────────────────────────────────────────────────────────
def call_openai(model, text):
    try:
        resp = openai_client.chat.completions.create(
            model=model,
            messages=[{"role":"system","content":SYSTEM_PROMPT},
                      {"role":"user","content":text}],
            temperature=0.7
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        raise RuntimeError(f"OpenAI[{model}] error: {e}")

def call_gemini(text):
    g = genai.GenerativeModel("models/gemini-1.5-flash") \
         .generate_content([{"role":"user","parts":[text]}])
    return getattr(g, "text", "").strip()

def call_mistral(text):
    r = requests.post(
        "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.1",
        headers={"Authorization":f"Bearer {HF_TOKEN}"},
        json={"inputs": text}, timeout=30
    )
    r.raise_for_status()
    return r.json()[0]["generated_text"].strip()

def call_openrouter(text):
    r = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization":f"Bearer {OR_KEY}",
            "HTTP-Referer":"https://kaizen-agent",
            "X-Title":"Kaizen Agent"
        },
        json={
            "model":"mistral",
            "messages":[
                {"role":"system","content":SYSTEM_PROMPT},
                {"role":"user","content":text}
            ]
        }, timeout=30
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()

def call_copilot(text):
    return call_openai("gpt-4o", text)

# ─── MONTAR PROVIDERS E FALLBACK ORDER ────────────────────────────────────────
ALL_PROVIDERS = {}
if GEMINI_KEY:     ALL_PROVIDERS["gemini"]        = call_gemini
if HF_TOKEN:       ALL_PROVIDERS["mistral"]       = call_mistral
if OR_KEY:         ALL_PROVIDERS["openrouter"]    = call_openrouter
if OPENAI_KEY:
    ALL_PROVIDERS["gpt-3.5-turbo"] = lambda t: call_openai("gpt-3.5-turbo", t)
    ALL_PROVIDERS["copilot"]       = call_copilot

FALLBACK_ORDER = [
    p for p in ["gemini", "mistral", "openrouter", "gpt-3.5-turbo", "copilot"]
    if p in ALL_PROVIDERS
]
_fallback_lock = threading.Lock()

# ─── COTAS, USO & CACHE ────────────────────────────────────────────────────────
usage_counters = {p: 0 for p in FALLBACK_ORDER}
DAILY_LIMITS   = {"gemini": 50}
CACHE          = {}

def within_limit(name):
    lim = DAILY_LIMITS.get(name)
    return True if lim is None else usage_counters[name] < lim

def cached(name, fn, text):
    key = (name, text)
    if key in CACHE:
        return CACHE[key]
    out = fn(text)
    CACHE[key] = out
    return out

# ─── BUILD CONTEXT ────────────────────────────────────────────────────────────
def build_context(channel, msg):
    mem = read_memory()
    hist = [m for m in mem if m["origem"] == channel]
    parts, size = [], 0
    for h in reversed(hist):
        blk = f"Usuário: {h['entrada']}\nKaizen: {h['resposta']}\n"
        if size + len(blk) > MAX_CTX * 0.8:
            break
        parts.insert(0, blk)
        size += len(blk)
    parts.append(f"Usuário: {msg}")
    ctx = SYSTEM_PROMPT + "\n" + "".join(parts)
    return ctx[-MAX_CTX:] if len(ctx) > MAX_CTX else ctx

# ─── FALLBACK ROBUSTO ──────────────────────────────────────────────────────────
def gerar_resposta(text):
    errors = {}
    with _fallback_lock:
        seq = FALLBACK_ORDER.copy()
    for name in seq:
        if not within_limit(name):
            errors[name] = "quota excedida"
            continue
        try:
            logging.info(f"[fallback] tentando {name}")
            out = cached(name, ALL_PROVIDERS[name], text)
            if not out or not out.strip():
                raise RuntimeError(f"{name} retornou vazio")
            # só agora incrementa após sucesso
            usage_counters[name] += 1
            # promove provider vencedor
            with _fallback_lock:
                FALLBACK_ORDER.remove(name)
                FALLBACK_ORDER.insert(0, name)
            return out
        except Exception as e:
            logging.warning(f"[fallback] {name} falhou: {e}")
            errors[name] = str(e)
    return f"⚠️ Nenhuma IA disponível. Erros: {errors}"

def gerar_resposta_com_memoria(channel, msg):
    ctx  = build_context(channel, msg)
    resp = gerar_resposta(ctx)
    if resp.startswith("⚠️"):
        return resp
    write_memory({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "origem":    channel,
        "entrada":   msg,
        "resposta":  resp
    })
    return resp

# ─── MEMÓRIA (Google Drive) ───────────────────────────────────────────────────
SCOPES      = ['https://www.googleapis.com/auth/drive']
MEM_FILE    = 'kaizen_memory_log.json'

def drive_service():
    creds = service_account.Credentials.from_service_account_info(
        GOOGLE_CREDS, scopes=SCOPES)
    return build_drive('drive', 'v3', credentials=creds, cache_discovery=False)

def get_file_id(svc):
    files = svc.files().list(
        q=f"name='{MEM_FILE}'", spaces='drive', fields='files(id)'
    ).execute().get('files', [])
    if not files:
        raise FileNotFoundError(MEM_FILE)
    return files[0]['id']

def read_memory():
    svc = drive_service()
    buf = io.BytesIO()
    done = False
    dl = MediaIoBaseDownload(buf, svc.files().get_media(fileId=get_file_id(svc)))
    while not done:
        _, done = dl.next_chunk()
    buf.seek(0)
    return json.load(buf)

def write_memory(entry):
    with MEMORY_LOCK:
        svc = drive_service()
        fid = get_file_id(svc)
        mem = read_memory()
        mem.append(entry)
        buf = io.BytesIO(json.dumps(mem, indent=2).encode())
        svc.files().update(fileId=fid,
                           media_body=MediaIoBaseUpload(buf,'application/json')).execute()

# ─── TELEGRAM & ROTAS ─────────────────────────────────────────────────────────
def send_telegram(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": text})
        if not r.ok:
            logging.error(f"[telegram] {r.status_code}: {r.text}")
    except Exception:
        logging.exception("[telegram] exception")

@app.route('/', methods=['GET'])
def index(): return "OK", 200

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json(force=True)
    msg  = data.get("message", "").strip()
    if not msg:
        return jsonify(error="mensagem vazia"), 400
    return jsonify(reply=gerar_resposta_com_memoria("web", msg))

@app.route('/usage', methods=['GET'])
def usage(): return jsonify(usage_counters)

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
    txt     = payload.get("text", "").strip()
    cid     = str(payload.get("chat", {}).get("id", ""))
    if not txt:
        send_telegram(cid, "⚠️ Mensagem vazia.")
    else:
        resp = gerar_resposta_com_memoria(f"tg:{cid}", txt)
        send_telegram(cid, resp)
    return jsonify(ok=True)

# ─── AUTONOMOUS LOOP (4h) ─────────────────────────────────────────────────────
def autonomous_loop():
    while True:
        try:
            insight = gerar_resposta_com_memoria("saito", "Gere um insight produtivo.")
            send_telegram(TELEGRAM_CHAT_ID, insight)
        except Exception:
            logging.exception("[autonomous] falhou")
        time.sleep(4 * 3600)

threading.Thread(target=autonomous_loop, daemon=True).start()

# ─── RESET DIÁRIO DAS COTAS ───────────────────────────────────────────────────
def reset_daily_counters():
    while True:
        now = datetime.now(timezone.utc)
        nxt = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        time.sleep((nxt - now).total_seconds())
        for k in usage_counters:
            usage_counters[k] = 0
        logging.info("[quota] contadores resetados")

threading.Thread(target=reset_daily_counters, daemon=True).start()

# ─── RUN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
