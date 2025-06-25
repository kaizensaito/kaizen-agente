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
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify
from twilio.rest import Client
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from openai.error import OpenAIError
from dotenv import load_dotenv

# ─── CONFIGURAÇÃO ─────────────────────────────────────────────────────────────
load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z"
)
app         = Flask(__name__)
CLIENT_TZ   = ZoneInfo("America/Sao_Paulo")
MEMORY_LOCK = threading.Lock()

# ─── IDENTIDADE / ALIASES ─────────────────────────────────────────────────────
def mapear_identidade(origem: str) -> str:
    if origem.startswith("whatsapp:"):
        return "usuario"
    if origem == "webhook":
        return "usuario"
    return origem

# ─── ENV VARS ─────────────────────────────────────────────────────────────────
# Twilio / WhatsApp
TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN  = os.environ["TWILIO_AUTH_TOKEN"]
FROM_WPP           = os.environ["FROM_WPP"]
TO_WPP             = os.environ["TO_WPP"]
client_twilio      = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# OpenAI dual-key pattern
OPT_KEY  = os.environ["OPENAI_API_KEY_OPTIMIZER"]  # GPT-3.5 free
MAIN_KEY = os.environ.get("OPENAI_API_KEY_MAIN", OPT_KEY)

# Gemini (fallback)
genai.configure(api_key=os.environ["GEMINI_API_KEY"])
GEMINI_MODELS = [
    "models/gemini-1.5-flash",
    "models/gemini-1.5-pro",
    "models/gemini-2.5-pro-preview-06-05"
]

# Google Drive (memória)
SCOPES         = ['https://www.googleapis.com/auth/drive']
JSON_FILE_NAME = 'kaizen_memory_log.json'
GOOGLE_CREDS   = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])

# Trello
TRELLO_KEY     = os.environ["TRELLO_KEY"]
TRELLO_TOKEN   = os.environ["TRELLO_TOKEN"]
TRELLO_LIST_ID = os.environ["TRELLO_LIST_ID"]
TRELLO_API_URL = "https://api.trello.com/1"

# ─── MEMÓRIA (Google Drive JSON) ───────────────────────────────────────────────
def drive_service():
    creds = service_account.Credentials.from_service_account_info(
        GOOGLE_CREDS, scopes=SCOPES
    )
    return build('drive', 'v3', credentials=creds)

def get_json_file_id(svc):
    files = svc.files().list(
        q=f"name='{JSON_FILE_NAME}'", spaces='drive', fields='files(id)'
    ).execute().get('files', [])
    if not files:
        raise FileNotFoundError(f"{JSON_FILE_NAME} não encontrado.")
    return files[0]['id']

def read_memory():
    svc = drive_service()
    req = svc.files().get_media(fileId=get_json_file_id(svc))
    buf = io.BytesIO()
    dl  = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        _, done = dl.next_chunk()
    buf.seek(0)
    return json.load(buf)

def write_memory(entry):
    with MEMORY_LOCK:
        svc   = drive_service()
        fid   = get_json_file_id(svc)
        mem   = read_memory()
        mem.append(entry)
        buf   = io.BytesIO(json.dumps(mem, indent=2).encode('utf-8'))
        media = MediaIoBaseUpload(buf, mimetype='application/json')
        svc.files().update(fileId=fid, media_body=media).execute()
        logging.info(f"[Memória] {entry['origem']} → gravado")

# ─── OPENAI HELPER (nova API) ──────────────────────────────────────────────────
def call_openai(api_key: str, model: str, messages: list, temperature: float = 0.7):
    prev_key = openai.api_key
    openai.api_key = api_key
    resp = openai.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature
    )
    openai.api_key = prev_key
    return resp

# ─── FUNÇÃO DE OTMIZAÇÃO DE PROMPT ─────────────────────────────────────────────
def otimizar_prompt(raw: str) -> str:
    # não comprimir textos muito curtos
    if len(raw) < 500:
        return raw

    # 1) Tenta GPT-3.5
    try:
        resp = call_openai(
            OPT_KEY,
            model="gpt-3.5-turbo",
            temperature=0.0,
            messages=[
                {"role":"system","content":
                    "Você é um compressor de texto. Receba qualquer input e devolva "
                    "a versão mais curta, clara e direta possível, preservando 100% do significado."
                },
                {"role":"user","content": raw}
            ]
        )
        return resp.choices[0].message.content.strip()
    except OpenAIError as e:
        if getattr(e, "code", "") == "insufficient_quota":
            logging.warning("[Otimização] sem quota GPT-3.5, fallback Gemini")
        else:
            logging.warning(f"[Otimização] GPT-3.5 falhou: {e}")

    # 2) Fallback: Gemini
    for model in GEMINI_MODELS:
        try:
            out = genai.GenerativeModel(model).generate_content([{
                "role":"user",
                "parts":[
                  f"Resuma este texto ao máximo, mantendo o sentido:\n\n{raw}"
                ]
            }])
            if getattr(out, "text", None):
                return out.text.strip()
        except Exception:
            continue

    # 3) Fallback local: limpeza simples
    text = re.sub(r'\n{2,}', '\n', raw)  # remove quebras duplas
    text = re.sub(r' +', ' ', text)      # remove multi-espaços
    parts = text.split('\n\n')
    return '\n\n'.join(parts[:3]) if len(parts) > 3 else text

# ─── PIPELINE DE GERAÇÃO ──────────────────────────────────────────────────────
def gerar_resposta(contexto: str) -> str:
    system_prompt = (
        "Você é o Kaizen, IA autônoma, direta e estratégica para Nilson Saito. "
        "Nada de floreios."
    )
    raw = f"{system_prompt}\n{contexto}"

    # pré-compressão
    prompt_otim = otimizar_prompt(raw)

    # tentativa principal: GPT-4
    try:
        resp = call_openai(
            MAIN_KEY,
            model="gpt-4o",
            messages=[
                {"role":"system","content":system_prompt},
                {"role":"user",  "content":prompt_otim}
            ]
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logging.warning(f"[GPT-4] falhou: {e}, partindo para fallback Gemini")

    # fallback final: Gemini
    for model in GEMINI_MODELS:
        try:
            out = genai.GenerativeModel(model).generate_content([{
                "role":"user","parts":[f"{system_prompt}\n{prompt_otim}"]
            }])
            if getattr(out, "text", None):
                return out.text.strip()
        except Exception:
            continue

    return "Erro geral: todos os modelos falharam."

def gerar_resposta_com_memoria(origem: str, msg: str) -> str:
    alias = mapear_identidade(origem)
    mem   = read_memory()
    hist  = [m for m in mem if mapear_identidade(m["origem"]) == alias][-10:]
    ctx   = "\n".join(f"Usuário: {m['entrada']}\nKaizen: {m['resposta']}" for m in hist)
    ctx  += f"\nUsuário: {msg}"
    resp = gerar_resposta(ctx)
    write_memory({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "origem":    origem,
        "entrada":   msg,
        "resposta":  resp
    })
    return resp

# ─── WHATSAPP ─────────────────────────────────────────────────────────────────
def enviar_whatsapp(to: str, msg: str):
    try:
        client_twilio.messages.create(body=msg, from_=FROM_WPP, to=to)
        logging.info(f"[WhatsApp] enviado → {to}")
    except Exception:
        logging.exception("[WhatsApp] falha ao enviar")

# ─── TRELLO ───────────────────────────────────────────────────────────────────
def criar_tarefa_trello(titulo: str, descricao: str = "", due_days: int = 1):
    due = (datetime.now(timezone.utc) + timedelta(days=due_days)) \
          .replace(hour=9, minute=0, second=0, microsecond=0) \
          .isoformat()
    payload = {
        "key":    TRELLO_KEY,
        "token":  TRELLO_TOKEN,
        "idList": TRELLO_LIST_ID,
        "name":   titulo[:100],
        "desc":   descricao,
        "due":    due,
        "pos":    "top"
    }
    try:
        r = requests.post(f"{TRELLO_API_URL}/cards", params=payload)
        r.raise_for_status()
        c = r.json()
        logging.info(f"[Trello] criado: {c['id']} → {titulo}")
    except Exception:
        logging.exception("[Trello] falha ao criar card")

# ─── CICLOS & MONITORAMENTO ───────────────────────────────────────────────────
def pensar_autonomamente():
    h = datetime.now(CLIENT_TZ).hour
    if   5  <= h < 9:    p = "Bom dia. Que atitude proativa tomaria hoje sem intervenção?"
    elif 12 <= h < 14:   p = "Hora do almoço. Revise sua performance e gere insight produtivo."
    elif 18 <= h < 20:   p = "Fim do expediente. O que aprendeu e pode otimizar amanhã?"
    else:                p = "Use seu julgamento. Execute algo útil com base no histórico."
    try:
        insight = gerar_resposta_com_memoria("saito", p)
        enviar_whatsapp(TO_WPP, insight)
        criar_tarefa_trello(insight.split("\n")[0], descricao=insight)
    except Exception as e:
        write_memory({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "origem":   "kaizen_autonomo",
            "entrada":  "erro_ciclo_autonomo",
            "resposta": str(e)
        })

def heartbeat():
    write_memory({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "origem":   "sistema",
        "entrada":  "heartbeat",
        "resposta": "Kaizen ativo."
    })

def check_render():
    try:
        st = requests.get("https://kaizen-agente.onrender.com/ask", timeout=5).status_code
        write_memory({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "origem":   "watchdog",
            "entrada":  "check_render",
            "resposta": f"status {st}"
        })
    except Exception as e:
        write_memory({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "origem":   "watchdog",
            "entrada":  "check_render",
            "resposta": str(e)
        })

def loop(fn, interval):
    while True:
        fn()
        time.sleep(interval)

def loop_relatorio():
    while True:
        now    = datetime.now(CLIENT_TZ)
        target = now.replace(hour=18, minute=0, second=0, microsecond=0)
        if now > target:
            target += timedelta(days=1)
        time.sleep((target - now).total_seconds())
        enviar_whatsapp(TO_WPP, "🧠 Kaizen rodando bem. Status diário OK.")

# ─── FLASK ROUTES ─────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return "✅ Kaizen ativo: memória, WhatsApp, Trello, autonomia."

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json(force=True)
    msg  = data.get("message", "").strip()
    if not msg:
        return jsonify({"error": "mensagem vazia"}), 400
    reply = gerar_resposta_com_memoria("webhook", msg)
    return jsonify({"reply": reply})

@app.route('/whatsapp_webhook', methods=['POST'])
def whatsapp_webhook():
    msg    = request.form.get("Body", "")
    sender = request.form.get("From", "")
    resp   = gerar_resposta_com_memoria(sender, msg)
    enviar_whatsapp(sender, resp)
    return "OK", 200

# ─── BOOT ────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    threading.Thread(target=loop, args=(heartbeat, 300), daemon=True).start()
    threading.Thread(target=loop, args=(check_render, 600), daemon=True).start()
    threading.Thread(target=loop_relatorio, daemon=True).start()
    threading.Thread(target=loop, args=(pensar_autonomamente, 3600), daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
