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

# ─── ALIASES ───────────────────────────────────────────────────────────────────
def mapear_identidade(origem: str) -> str:
    if origem.startswith("tg:") or origem == "webhook":
        return "usuario"
    return origem

# ─── ENV VARS ─────────────────────────────────────────────────────────────────
OPT_KEY             = os.environ["OPENAI_API_KEY_OPTIMIZER"]
MAIN_KEY            = os.environ.get("OPENAI_API_KEY_MAIN", OPT_KEY)
genai.configure(api_key=os.environ["GEMINI_API_KEY"])
GEMINI_MODELS       = [
    "models/gemini-1.5-flash",
    "models/gemini-1.5-pro",
    "models/gemini-2.5-pro-preview-06-05"
]

SCOPES              = ['https://www.googleapis.com/auth/drive']
JSON_FILE_NAME      = 'kaizen_memory_log.json'
GOOGLE_CREDS        = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
GOOGLE_CALENDAR_ID  = os.environ.get("GOOGLE_CALENDAR_ID", "primary")

TRELLO_KEY          = os.environ["TRELLO_KEY"]
TRELLO_TOKEN        = os.environ["TRELLO_TOKEN"]
TRELLO_LIST_ID      = os.environ["TRELLO_LIST_ID"]
TRELLO_API_URL      = "https://api.trello.com/1"

TELEGRAM_TOKEN      = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_LOOP_ID    = os.environ["TELEGRAM_CHAT_ID"]
TELEGRAM_URL        = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

# ─── MEMÓRIA (Google Drive JSON) ───────────────────────────────────────────────
def drive_service():
    creds = service_account.Credentials.from_service_account_info(
        GOOGLE_CREDS, scopes=SCOPES
    )
    return build_drive('drive', 'v3', credentials=creds)

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
    buf = io.BytesIO(); dl = MediaIoBaseDownload(buf, req); done=False
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
        buf   = io.BytesIO(json.dumps(mem, indent=2).encode())
        media = MediaIoBaseUpload(buf, mimetype='application/json')
        svc.files().update(fileId=fid, media_body=media).execute()
        logging.info(f"[Memória] {entry['origem']} → gravado")

# ─── CALENDAR & PARSER ─────────────────────────────────────────────────────────
def calendar_service():
    creds = service_account.Credentials.from_service_account_info(
        GOOGLE_CREDS, scopes=['https://www.googleapis.com/auth/calendar']
    )
    return build_cal('calendar', 'v3', credentials=creds)

def criar_evento_calendar(summary: str, start_dt: datetime, end_dt: datetime):
    svc = calendar_service()
    body = {
        'summary': summary,
        'start': {'dateTime': start_dt.isoformat(), 'timeZone':'America/Sao_Paulo'},
        'end':   {'dateTime': end_dt.isoformat(),   'timeZone':'America/Sao_Paulo'},
    }
    ev = svc.events().insert(calendarId=GOOGLE_CALENDAR_ID, body=body).execute()
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
        OPT_KEY, model="gpt-3.5-turbo",
        messages=[{"role":"system","content":prompt},{"role":"user","content":text}],
        temperature=0
    )
    return json.loads(resp.choices[0].message.content.strip())

# ─── OPENAI & OTIMIZAÇÃO ───────────────────────────────────────────────────────
def call_openai(api_key: str, model: str, messages: list, temperature: float = 0.7):
    prev, openai.api_key = openai.api_key, api_key
    resp = openai.chat.completions.create(
        model=model, messages=messages, temperature=temperature
    )
    openai.api_key = prev
    return resp

def otimizar_prompt(raw: str) -> str:
    if len(raw) < 500:
        return raw
    try:
        resp = call_openai(
            OPT_KEY, model="gpt-3.5-turbo", temperature=0.0,
            messages=[
                {"role":"system","content":
                 "Você é um compressor de texto. Retorne a versão mais curta,"
                 " clara e direta, preservando 100% do significado."},
                {"role":"user","content": raw}
            ]
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logging.warning(f"[Otimização] falhou: {e}")
    for model in GEMINI_MODELS:
        try:
            out = genai.GenerativeModel(model).generate_content([{
                "role":"user","parts":[f"Resuma este texto:\n\n{raw}"]
            }])
            if getattr(out, "text", None):
                return out.text.strip()
        except:
            continue
    t = re.sub(r'\n{2,}', '\n', raw)
    t = re.sub(r' +', ' ', t)
    p = t.split('\n\n')
    return '\n\n'.join(p[:3]) if len(p)>3 else t

def gerar_resposta(contexto: str) -> str:
    system_prompt = "Você é o Kaizen, IA autônoma e estratégica para Nilson Saito."
    raw = f"{system_prompt}\n{contexto}"
    prompt_otim = otimizar_prompt(raw)
    try:
        resp = call_openai(
            MAIN_KEY, model="gpt-4o",
            messages=[{"role":"system","content":system_prompt},
                      {"role":"user","content":prompt_otim}]
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
        except:
            continue
    return "Desculpe, estou sem acesso a modelos no momento."

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

# ─── TRELLO & CARTÕES ─────────────────────────────────────────────────────────
def criar_tarefa_trello(titulo: str, descricao: str = "", due_days: int = 1):
    due = (datetime.now(timezone.utc) + timedelta(days=due_days))\
          .replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
    payload = {
        "key":    TRELLO_KEY, "token":  TRELLO_TOKEN,
        "idList": TRELLO_LIST_ID, "name": titulo[:100],
        "desc":   descricao,       "due":    due, "pos": "top"
    }
    try:
        r = requests.post(f"{TRELLO_API_URL}/cards", params=payload)
        r.raise_for_status()
        logging.info(f"[Trello] criado: {r.json()['id']} → {titulo}")
    except Exception:
        logging.exception("[Trello] falha ao criar card")

def parse_card_request(text: str) -> dict:
    m = re.match(
        r".*criar (?:cartão|card)(?: chamado)?\s*([^:]+)(?::\s*(.+))?",
        text, flags=re.IGNORECASE
    )
    if m:
        return {"title": m.group(1).strip(), "desc": (m.group(2) or "").strip()}
    return {"title": text, "desc": ""}

# ─── TELEGRAM ─────────────────────────────────────────────────────────────────
def enviar_telegram(chat_id: str, msg: str):
    try:
        requests.post(
            TELEGRAM_URL, json={"chat_id": chat_id, "text": msg}
        ).raise_for_status()
        logging.info(f"[Telegram] enviado → {chat_id}")
    except Exception:
        logging.exception("[Telegram] falha ao enviar")

@app.route('/telegram_webhook', methods=['POST'])
def telegram_webhook():
    payload = request.get_json(force=True)
    logging.info(f"[Telegram Webhook] payload: {payload}")
    try:
        msg    = payload["message"].get("text", "")
        chatid = str(payload["message"]["chat"]["id"])
        lower  = msg.lower()

        if "criar cartão" in lower or "criar card" in lower:
            card = parse_card_request(msg)
            criar_tarefa_trello(card["title"], card["desc"])
            resp = f"✅ Cartão '{card['title']}' criado no Trello."
        elif "criar evento" in lower:
            ev    = parse_event_request(msg)
            start = dt_parse(ev["start"]); end = dt_parse(ev["end"])
            criar_evento_calendar(ev["title"], start, end)
            resp = (
                f"✅ Evento '{ev['title']}' criado:\n"
                f"{start.strftime('%Y-%m-%d %H:%M')}–{end.strftime('%H:%M')}"
            )
        else:
            try:
                resp = gerar_resposta_com_memoria(f"tg:{chatid}", msg)
            except Exception as e:
                logging.warning(f"[Kaizen] erro ao gerar resposta: {e}")
                resp = "Desculpe, estou sem acesso agora. Tente novamente mais tarde."

        enviar_telegram(chatid, resp)
    except Exception:
        logging.exception("[Telegram Webhook] erro no processamento")
    return jsonify({"ok": True})

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json(force=True)
    msg  = data.get("message", "").strip()
    if not msg:
        return jsonify({"error":"mensagem vazia"}), 400
    lower = msg.lower()

    if "criar cartão" in lower or "criar card" in lower:
        card = parse_card_request(msg)
        criar_tarefa_trello(card["title"], card["desc"])
        return jsonify({"status":"ok","message":f"Cartão '{card['title']}' criado."})

    if "criar evento" in lower:
        ev      = parse_event_request(msg)
        start   = dt_parse(ev["start"]); end = dt_parse(ev["end"])
        created = criar_evento_calendar(ev["title"], start, end)
        return jsonify({
            "status":"ok",
            "message": f"Evento '{ev['title']}' criado: {start}–{end}.",
            "id": created["id"]
        })

    try:
        reply = gerar_resposta_com_memoria("webhook", msg)
    except Exception as e:
        logging.warning(f"[Kaizen] erro ao gerar resposta via /ask: {e}")
        reply = "Desculpe, estou sem acesso agora. Tente novamente mais tarde."
    return jsonify({"reply": reply})

# ─── CICLOS & MONITORAMENTO ───────────────────────────────────────────────────
def pensar_autonomamente():
    h = datetime.now(CLIENT_TZ).hour
    if 5 <= h < 9:
        p = "Bom dia. Que atitude proativa tomaria hoje sem intervenção?"
    elif 12 <= h < 14:
        p = "Hora do almoço. Revise sua performance e gere insight produtivo."
    elif 18 <= h < 20:
        p = "Fim de expediente. O que aprendeu e pode otimizar amanhã?"
    else:
        p = "Use seu julgamento. Execute algo útil com base no histórico."
    try:
        insight = gerar_resposta_com_memoria("saito", p)
        enviar_telegram(TELEGRAM_LOOP_ID, insight)
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
        enviar_telegram(TELEGRAM_LOOP_ID, "🧠 Relatório diário: Kaizen rodando bem.")

# ─── BOOT & THREADS ───────────────────────────────────────────────────────────
if __name__ == '__main__':
    threading.Thread(target=loop, args=(heartbeat, 300), daemon=True).start()
    threading.Thread(target=loop, args=(check_render, 600), daemon=True).start()
    threading.Thread(target=loop_relatorio, daemon=True).start()
    threading.Thread(target=loop, args=(pensar_autonomamente, 3600), daemon=True).start()

    # notificação única no boot
    enviar_telegram(TELEGRAM_LOOP_ID, "🟢 Kaizen iniciado e pronto.")

    # check-ins fixos: manhã (09:00) e noite (21:00)
    def schedule_message(hour: int, minute: int, text: str):
        while True:
            now = datetime.now(CLIENT_TZ)
            target = now.replace(hour=hour, minute=minute,
                                 second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            time.sleep((target - now).total_seconds())
            enviar_telegram(TELEGRAM_LOOP_ID, text)

    threading.Thread(target=schedule_message, args=(9, 0,  "☀️ Bom dia
