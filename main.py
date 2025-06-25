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
from google.oauth2 import service_account
from googleapiclient.discovery import build as build_drive, build as build_cal
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
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
    if origem.startswith("tg:"):
        return "usuario"
    if origem == "webhook":
        return "usuario"
    return origem

# ─── ENV VARS ─────────────────────────────────────────────────────────────────
# OpenAI
OPT_KEY        = os.environ["OPENAI_API_KEY_OPTIMIZER"]
MAIN_KEY       = os.environ.get("OPENAI_API_KEY_MAIN", OPT_KEY)

# Gemini fallback
genai.configure(api_key=os.environ["GEMINI_API_KEY"])
GEMINI_MODELS  = [
    "models/gemini-1.5-flash",
    "models/gemini-1.5-pro",
    "models/gemini-2.5-pro-preview-06-05"
]

# Google Drive memory
SCOPES            = ['https://www.googleapis.com/auth/drive']
JSON_FILE_NAME    = 'kaizen_memory_log.json'
GOOGLE_CREDS      = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])

# Google Calendar
GOOGLE_CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "primary")

# Trello
TRELLO_KEY      = os.environ["TRELLO_KEY"]
TRELLO_TOKEN    = os.environ["TRELLO_TOKEN"]
TRELLO_LIST_ID  = os.environ["TRELLO_LIST_ID"]
TRELLO_API_URL  = "https://api.trello.com/1"

# Telegram
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_LOOP_ID = os.environ["TELEGRAM_CHAT_ID"]
TELEGRAM_URL     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

# ─── MEMÓRIA (Google Drive JSON) ───────────────────────────────────────────────
def drive_service():
    creds = service_account.Credentials.from_service_account_info(
        GOOGLE_CREDS, scopes=SCOPES
    )
    return build_drive('drive', 'v3', credentials=creds)

def get_json_file_id(svc):
    files = svc.files().list(
        q=f"name='{JSON_FILE_NAME}'", spaces='drive',
        fields='files(id)'
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

# ─── CALENDAR SERVICE & PARSER ────────────────────────────────────────────────
def calendar_service():
    creds = service_account.Credentials.from_service_account_info(
        GOOGLE_CREDS,
        scopes=['https://www.googleapis.com/auth/calendar']
    )
    return build_cal('calendar', 'v3', credentials=creds)

def criar_evento_calendar(summary: str, start_dt: datetime, end_dt: datetime):
    svc = calendar_service()
    body = {
        'summary': summary,
        'start': {'dateTime': start_dt.isoformat(), 'timeZone':'America/Sao_Paulo'},
        'end':   {'dateTime': end_dt.isoformat(),   'timeZone':'America/Sao_Paulo'},
    }
    ev = svc.events().insert(
        calendarId=GOOGLE_CALENDAR_ID,
        body=body
    ).execute()
    logging.info(f"[Calendar] evento criado: {ev['id']} → {summary}")
    return ev

def parse_event_request(text: str) -> dict:
    prompt = (
        "Você é um parser de eventos. Recebe uma frase como:\n\n"
        "  criar evento trabalho amanhã das 8:00 às 20:00\n\n"
        "Retorne apenas JSON com "
        "{\"title\":...,\"start\":...,\"end\":...} em ISO 8601, sem texto adicional."
    )
    resp = call_openai(
        OPT_KEY,
        model="gpt-3.5-turbo",
        messages=[
            {"role":"system","content":prompt},
            {"role":"user","content":text}
        ],
        temperature=0
    )
    return json.loads(resp.choices[0].message.content.strip())

# ─── OPENAI HELPER (nova API) ──────────────────────────────────────────────────
def call_openai(api_key: str, model: str, messages: list, temperature: float = 0.7):
    prev = openai.api_key
    openai.api_key = api_key
    resp = openai.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature
    )
    openai.api_key = prev
    return resp

# ─── FUNÇÃO DE OTIMIZAÇÃO DE PROMPT ────────────────────────────────────────────
def otimizar_prompt(raw: str) -> str:
    if len(raw) < 500:
        return raw

    try:
        resp = call_openai(
            OPT_KEY,
            model="gpt-3.5-turbo",
            temperature=0.0,
            messages=[
                {"role":"system","content":
                    "Você é um compressor de texto. Devolva a versão mais curta,"
                    " clara e direta possível, preservando 100% do significado."
                },
                {"role":"user","content": raw}
            ]
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        code = getattr(e, "code", "")
        if code == "insufficient_quota":
            logging.warning("[Otimização] sem quota GPT-3.5, fallback Gemini")
        else:
            logging.warning(f"[Otimização] falhou: {e}")

    for model in GEMINI_MODELS:
        try:
            out = genai.GenerativeModel(model).generate_content([{
                "role":"user",
                "parts":[f"Resuma este texto ao máximo, mantendo o sentido:\n\n{raw}"]
            }])
            if getattr(out, "text", None):
                return out.text.strip()
        except Exception:
            continue

    text = re.sub(r'\n{2,}', '\n', raw)
    text = re.sub(r' +', ' ', text)
    parts = text.split('\n\n')
    return '\n\n'.join(parts[:3]) if len(parts)>3 else text

# ─── PIPELINE DE GERAÇÃO ──────────────────────────────────────────────────────
def gerar_resposta(contexto: str) -> str:
    system_prompt = (
        "Você é o Kaizen, IA autônoma, direta e estratégica para Nilson Saito. Nada de floreios."
    )
    raw = f"{system_prompt}\n{contexto}"
    prompt_otim = otimizar_prompt(raw)

    try:
        resp = call_openai(
            MAIN_KEY,
            model="gpt-4o",
            messages=[
                {"role":"system","content":system_prompt},
                {"role":"user","content":prompt_otim}
            ]
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logging.warning(f"[GPT-4] falhou: {e}, fallback Gemini")

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
    hist  = [m for m in mem if mapear_identidade(m["origem"])==alias][-10:]
    ctx   = "\n".join(f"Usuário: {m['entrada']}\nKaizen: {m['resposta']}" for m in hist)
    ctx  += f"\nUsuário: {msg}"
    resp  = gerar_resposta(ctx)
    write_memory({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "origem":    origem,
        "entrada":   msg,
        "resposta":  resp
    })
    return resp

# ─── TELEGRAM ──────────────────────────────────────────────────────────────────
def enviar_telegram(chat_id: str, msg: str):
    try:
        requests.post(
            TELEGRAM_URL,
            json={"chat_id": chat_id, "text": msg}
        ).raise_for_status()
        logging.info(f"[Telegram] enviado → {chat_id}")
    except Exception:
        logging.exception("[Telegram] falha ao enviar")

@app.route('/telegram_webhook', methods=['POST'])
def telegram_webhook():
    upd    = request.get_json(force=True)
    msg    = upd["message"].get("text","")
    chatid = str(upd["message"]["chat"]["id"])

    lower = msg.lower()
    if "criar evento" in lower:
        ev    = parse_event_request(msg)
        start = dt_parse(ev["start"])
        end   = dt_parse(ev["end"])
        criar_evento_calendar(ev["title"], start, end)
        resp = (
            f"✅ Evento '{ev['title']}' criado:\n"
            f"{start.strftime('%Y-%m-%d %H:%M')}–{end.strftime('%H:%M')}"
        )
    else:
        resp = gerar_resposta_com_memoria(f"tg:{chatid}", msg)

    enviar_telegram(chatid, resp)
    return jsonify({"ok": True})

# ─── HTTP /ask (para clientes REST) ───────────────────────────────────────────
@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json(force=True)
    msg  = data.get("message","").strip()
    if not msg:
        return jsonify({"error":"mensagem vazia"}), 400

    lower = msg.lower()
    if "criar evento" in lower:
        ev    = parse_event_request(msg)
        start = dt_parse(ev["start"])
        end   = dt_parse(ev["end"])
        created = criar_evento_calendar(ev["title"], start, end)
        return jsonify({
            "status":"ok",
            "message": f"Evento '{ev['title']}' criado: {start}–{end}.",
            "id": created["id"]
        })

    reply = gerar_resposta_com_memoria("webhook", msg)
    return jsonify({"reply": reply})

# ─── CICLOS & MONITORAMENTO ───────────────────────────────────────────────────
def pensar_autonomamente():
    h = datetime.now(CLIENT_TZ).hour
    if   5  <= h < 9:    prompt = "Bom dia. Que atitude proativa tomaria hoje sem intervenção?"
    elif 12 <= h < 14:   prompt = "Hora do almoço. Revise sua performance e gere insight produtivo."
    elif 18 <= h < 20:   prompt = "Fim de expediente. O que aprendeu e pode otimizar amanhã?"
    else:                prompt = "Use seu julgamento. Execute algo útil com base no histórico."
    try:
        insight = gerar_resposta_com_memoria("saito", prompt)
        enviar_telegram(TELEGRAM_LOOP_ID, insight)
        # cria tarefa Trello
        criar_tarefa_trello(insight.split("\n")[0], descricao=insight)
    except Exception as e:
        write_memory({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "origem":   "kaizen_autonomo",
            "entrada":  "erro_autonomo",
            "resposta": str(e)
        })

def heartbeat():
    write_memory({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "origem":   "sistema",
        "entrada":  "heartbeat",
        "resposta": "Kaizen ativo."
    })
    enviar_telegram(TELEGRAM_LOOP_ID, "🟢 Kaizen ativo.")

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
        enviar_telegram(TELEGRAM_LOOP_ID, "🧠 Status diário OK.")

# ─── BOOT ────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    threading.Thread(target=loop, args=(heartbeat, 300), daemon=True).start()
    threading.Thread(target=loop, args=(check_render, 600), daemon=True).start()
    threading.Thread(target=loop_relatorio, daemon=True).start()
    threading.Thread(target=loop, args=(pensar_autonomamente, 3600), daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
