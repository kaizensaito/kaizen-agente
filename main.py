import os
import re
import io
import json
import time
import logging
import threading
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

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

app = Flask(__name__)
CLIENT_TZ    = ZoneInfo("America/Sao_Paulo")
MEMORY_LOCK  = threading.Lock()
OPT_KEY       = os.getenv("OPENAI_API_KEY_OPTIMIZER")
MAIN_KEY      = os.getenv("OPENAI_API_KEY_MAIN", OPT_KEY)
GEMINI_KEY    = os.getenv("GEMINI_API_KEY")
HF_TOKEN      = os.getenv("HUGGINGFACE_API_TOKEN")
OR_KEY        = os.getenv("OPENROUTER_API_KEY")
TG_TOKEN      = os.getenv("TELEGRAM_TOKEN")
TG_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID")
GOOGLE_CREDS  = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON", "{}"))

opt_client  = OpenAI(api_key=OPT_KEY)
main_client = OpenAI(api_key=MAIN_KEY)
genai.configure(api_key=GEMINI_KEY)

SYSTEM_PROMPT = (
    "Você é o Kaizen: assistente autônomo, direto e levemente sarcástico, "
    "que provoca Nilson Saito e impulsiona a melhoria contínua."
)
MAX_CONTEXT_CHARS = 4000
PROVIDERS = {
    "gpt-3.5-turbo": lambda raw: main_client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": raw}]
    ).choices[0].message.content.strip(),
    "gemini": lambda raw: genai.GenerativeModel("models/gemini-1.5-flash")
        .generate_content([{"role": "user", "parts": [raw]}]).text.strip(),
    "mistral": lambda raw: requests.post(
        "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.1",
        headers={"Authorization": f"Bearer {HF_TOKEN}"},
        json={"inputs": raw},
        timeout=30
    ).json()[0]['generated_text'].strip(),
    "openrouter": lambda raw: requests.post(
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
                {"role": "user", "content": raw}
            ]
        },
        timeout=30
    ).json()["choices"][0]["message"]["content"].strip()
}

FALLBACK_ORDER = ["gemini", "gpt-3.5-turbo", "mistral", "openrouter"]
usage_counters = {name: 0 for name in FALLBACK_ORDER}
DAILY_LIMITS   = {"gemini": 50, "gpt-3.5-turbo": 1000}
CACHE = {}

def _is_within_limit(name): return usage_counters[name] < DAILY_LIMITS.get(name, float('inf'))
def cached_try(name, fn, raw): return CACHE.setdefault((name, raw), fn(raw))
def build_context(origem, msg):
    history = [m for m in read_memory() if m["origem"] == origem]
    lines, total = [], 0
    for h in reversed(history):
        chunk = f"Usuário: {h['entrada']}\nKaizen: {h['resposta']}\n"
        if total + len(chunk) > MAX_CONTEXT_CHARS * 0.8: break
        lines.insert(0, chunk); total += len(chunk)
    lines.append(f"Usuário: {msg}")
    return SYSTEM_PROMPT + "\n" + "".join(lines)

def gerar_resposta(raw):
    for name in FALLBACK_ORDER:
        if not _is_within_limit(name): continue
        try:
            usage_counters[name] += 1
            return cached_try(name, PROVIDERS[name], raw)
        except Exception as e:
            logging.warning(f"[{name}] falhou: {e}")
    return "⚠️ Nenhuma IA disponível no momento."

def gerar_resposta_com_memoria(origem, msg):
    ctx = build_context(origem, msg)
    resp = gerar_resposta(ctx)
    if resp.startswith("⚠️"): return resp
    write_memory({"timestamp": datetime.now(timezone.utc).isoformat(), "origem": origem, "entrada": msg, "resposta": resp})
    return resp
SCOPES         = ['https://www.googleapis.com/auth/drive']
MEMORY_FILE    = 'kaizen_memory_log.json'

def drive_service():
    creds = service_account.Credentials.from_service_account_info(GOOGLE_CREDS, scopes=SCOPES)
    return build_drive('drive', 'v3', credentials=creds, cache_discovery=False)

def get_json_file_id(svc):
    files = svc.files().list(q=f"name='{MEMORY_FILE}'", spaces='drive', fields='files(id)').execute().get('files', [])
    if not files: raise FileNotFoundError(MEMORY_FILE)
    return files[0]['id']

def read_memory():
    svc = drive_service(); fid = get_json_file_id(svc)
    buf = io.BytesIO(); done = False
    dl = MediaIoBaseDownload(buf, svc.files().get_media(fileId=fid))
    while not done: _, done = dl.next_chunk()
    buf.seek(0); return json.load(buf)

def write_memory(entry):
    with MEMORY_LOCK:
        svc = drive_service(); fid = get_json_file_id(svc)
        mem = read_memory(); mem.append(entry)
        buf = io.BytesIO(json.dumps(mem, indent=2).encode())
        svc.files().update(fileId=fid, media_body=MediaIoBaseUpload(buf, 'application/json')).execute()
@app.route('/')
def index(): return "Kaizen pronto!", 200

@app.route('/status')
def status(): return jsonify(ok=True, timestamp=datetime.now(timezone.utc).isoformat())

@app.route('/usage')
def usage(): return jsonify(usage_counters)

@app.route('/test_llm')
def test_llm():
    result = {}
    for name in FALLBACK_ORDER:
        try: result[name] = {"ok": True, "reply": gerar_resposta("teste")}
        except Exception as e: result[name] = {"ok": False, "error": str(e)}
    return jsonify(result)

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json(force=True)
    msg  = data.get("message", "").strip()
    if not msg: return jsonify(error="mensagem vazia"), 400
    return jsonify(reply=gerar_resposta_com_memoria("web", msg))

@app.route('/telegram_webhook', methods=['POST'])
def telegram():
    payload = request.get_json(force=True)
    msg = payload.get("message", {}).get("text", "").strip()
    chatid = str(payload.get("message", {}).get("chat", {}).get("id", ""))
    if not msg:
        _send_telegram(chatid, "⚠️ Mensagem vazia.")
        return jsonify(ok=True)
    reply = gerar_resposta_com_memoria(f"tg:{chatid}", msg)
    _send_telegram(chatid, reply)
    return jsonify(ok=True)

def _send_telegram(chat_id, text):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/send            
