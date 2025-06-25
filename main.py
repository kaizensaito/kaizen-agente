import os
import re
import json
import io
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
from googleapiclient.discovery import build as build_drive, build as build_cal
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

# ─── CONFIGURAÇÃO & LOGGING ───────────────────────────────────────────────────
load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z"
)

app = Flask(__name__)
CLIENT_TZ = ZoneInfo("America/Sao_Paulo")
MEMORY_LOCK = threading.Lock()

# ─── VARIÁVEIS DE AMBIENTE ────────────────────────────────────────────────────
OPT_KEY = os.getenv("OPENAI_API_KEY_OPTIMIZER")
MAIN_KEY = os.getenv("OPENAI_API_KEY_MAIN", OPT_KEY)
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
HUGGINGFACE_TOKEN = os.getenv("HUGGINGFACE_API_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

TRELLO_KEY = os.getenv("TRELLO_KEY")
TRELLO_TOKEN = os.getenv("TRELLO_TOKEN")
TRELLO_LIST_ID = os.getenv("TRELLO_LIST_ID")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_LOOP_ID = os.getenv("TELEGRAM_CHAT_ID")

GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "{}")
GOOGLE_CREDS = json.loads(GOOGLE_CREDS_JSON)

# ─── CLIENTES LLM ─────────────────────────────────────────────────────────────
opt_client = OpenAI(api_key=OPT_KEY)
main_client = OpenAI(api_key=MAIN_KEY)
genai.configure(api_key=GEMINI_KEY)

GEMINI_MODELS = [
    "models/gemini-1.5-flash",
    "models/gemini-1.5-pro",
    "models/gemini-2.5-pro-preview-06-05"
]

_available_providers = ["gpt-4o", "gpt-3.5-turbo", "gemini", "mistral", "openrouter"]
_provider_lock = threading.Lock()

SYSTEM_PROMPT = (
    "Você é o Kaizen: assistente autônomo, direto e levemente sarcástico, "
    "que provoca Nilson Saito e impulsiona a melhoria contínua."
)
MAX_CONTEXT_CHARS = 4000

# ─── FUNÇÕES DE IA ────────────────────────────────────────────────────────────
def call_openai(client, model, messages, temperature=0.7, max_tokens=1024):
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens
    )
    return resp.choices[0].message.content.strip()

def _try_gpt4o(raw):
    return call_openai(main_client, "gpt-4o", [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": raw}])

def _try_gpt35(raw):
    return call_openai(main_client, "gpt-3.5-turbo", [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": raw}])

def _try_gemini(raw):
    for m in GEMINI_MODELS:
        try:
            g = genai.GenerativeModel(m).generate_content([{"role": "user", "parts": [raw]}])
            text = getattr(g, "text", None)
            if isinstance(text, str) and text.strip():
                return text.strip()
        except Exception as e:
            logging.warning(f"[gemini] {m} falhou: {e}")
    raise RuntimeError("Gemini sem resposta")

def _try_mistral(raw):
    headers = {"Authorization": f"Bearer {HUGGINGFACE_TOKEN}"}
    payload = {"inputs": raw}
    response = requests.post(
        "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.1",
        headers=headers, json=payload, timeout=30
    )
    response.raise_for_status()
    return response.json()[0]['generated_text'].strip()

def _try_openrouter(raw):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": "https://kaizen-agent",
        "X-Title": "Kaizen Agent"
    }
    data = {
        "model": "mistral",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": raw}
        ]
    }
    resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data, timeout=30)
    resp.raise_for_status()
    return resp.json()['choices'][0]['message']['content'].strip()

PROVIDERS = {
    "gpt-4o": _try_gpt4o,
    "gpt-3.5-turbo": _try_gpt35,
    "gemini": _try_gemini,
    "mistral": _try_mistral,
    "openrouter": _try_openrouter
}

# ─── CONTEXTO ─────────────────────────────────────────────────────────────────
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

# ─── RESPOSTA & MEMÓRIA ───────────────────────────────────────────────────────
def gerar_resposta(raw: str) -> str:
    with _provider_lock:
        order = list(_available_providers)
    for name in order:
        try:
            logging.info(f"[responder] tentando {name}")
            return PROVIDERS[name](raw)
        except Exception:
            logging.exception(f"[responder] {name} falhou")
    return "⚠️ Nenhuma IA disponível no momento."

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

# ─── MEMÓRIA (GOOGLE DRIVE) ───────────────────────────────────────────────────
SCOPES = ['https://www.googleapis.com/auth/drive']
MEMORY_FILE = 'kaizen_memory_log.json'

def drive_service():
    creds = service_account.Credentials.from_service_account_info(GOOGLE_CREDS, scopes=SCOPES)
    return build_drive('drive', 'v3', credentials=creds, cache_discovery=False)

def get_json_file_id(svc):
    files = svc.files().list(q=f"name='{MEMORY_FILE}'", spaces='drive', fields='files(id)').execute().get('files',[])
    if not files:
        raise FileNotFoundError(MEMORY_FILE)
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

# ─── ROTAS ─────────────────────────────────────────────────────────────────────
@app.route('/', methods=['GET'])
def index():
    return "Kaizen Agent rodando", 200

@app.route('/status', methods=['GET'])
def status():
    return jsonify(ok=True, timestamp=datetime.now(timezone.utc).isoformat())

@app.route('/ask', methods=['POST'])
def ask_post():
    data = request.get_json(force=True)
    msg  = data.get("message", "").strip()
    if not msg:
        return jsonify(error="mensagem vazia"), 400
    reply = gerar_resposta_com_memoria("web", msg)
    return jsonify(reply=reply)

# ─── EXECUÇÃO ──────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.getenv("PORT", "10000"))
    app.run(host='0.0.0.0', port=port)
