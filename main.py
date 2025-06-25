import os
import json
import time
import threading
import logging
import requests
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify
from twilio.rest import Client
import google.generativeai as genai
import openai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
import io
from dotenv import load_dotenv

# 1) Carregamento e config
load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z"
)

app = Flask(__name__)
CLIENT_TZ    = ZoneInfo("America/Sao_Paulo")
MEMORY_LOCK  = threading.Lock()

# 2) Twilio
TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN  = os.environ["TWILIO_AUTH_TOKEN"]
FROM_WPP           = os.getenv("FROM_WPP", "whatsapp:+14155238886")
TO_WPP             = os.getenv("TO_WPP", "whatsapp:+5511940217504")
client_twilio      = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# 3) Gemini + OpenAI
genai.configure(api_key=os.environ["GEMINI_API_KEY"])
GEMINI_MODELS = [
    "models/gemini-1.5-flash",
    "models/gemini-1.5-pro",
    "models/gemini-2.5-pro-preview-06-05"
]
openai.api_key = os.environ["OPENAI_API_KEY"]

# 4) Google Drive
SCOPES         = ['https://www.googleapis.com/auth/drive']
JSON_FILE_NAME = 'kaizen_memory_log.json'
GOOGLE_CREDS   = os.environ["GOOGLE_CREDENTIALS_JSON"]  # JSON string

# 5) Trello Config
TRELLO_KEY     = os.environ.get("TRELLO_KEY")
TRELLO_TOKEN   = os.environ.get("TRELLO_TOKEN")
TRELLO_BOARD_ID= os.environ.get("TRELLO_BOARD_ID")
TRELLO_LIST_ID = os.environ.get("TRELLO_LIST_ID")
TRELLO_API_URL = "https://api.trello.com/1"

# 6) Intervalos
CHECK_INTERVAL    = 300   # 5min
WATCHDOG_INTERVAL = 600   # 10min

# ================= MEMÓRIA =================

def drive_service():
    creds = service_account.Credentials.from_service_account_info(
        json.loads(GOOGLE_CREDS), scopes=SCOPES
    )
    return build('drive', 'v3', credentials=creds)

def get_json_file_id(service):
    results = service.files().list(
        q=f"name='{JSON_FILE_NAME}'",
        spaces='drive',
        fields='files(id,name)'
    ).execute()
    files = results.get('files', [])
    if not files:
        raise FileNotFoundError(f"{JSON_FILE_NAME} não encontrado no Drive.")
    return files[0]['id']

def read_memory():
    service = drive_service()
    file_id = get_json_file_id(service)
    request_media = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request_media)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    fh.seek(0)
    return json.load(fh)

def write_memory(entry):
    with MEMORY_LOCK:
        service = drive_service()
        file_id = get_json_file_id(service)
        memory = read_memory()
        memory.append(entry)
        buffer = io.BytesIO(json.dumps(memory, indent=2).encode('utf-8'))
        media  = MediaIoBaseUpload(buffer, mimetype='application/json')
        service.files().update(fileId=file_id, media_body=media).execute()
        logging.info(f"Memória atualizada: {entry['origem']} @ {entry['timestamp']}")

# ================= IDENTIDADE =================

ALIASES = {
    "saito": [
        "whatsapp:+5511940217504",
        "whatsapp:+5511934385115",
        TO_WPP,
        "cli",
        "webhook",
        "sistema"
    ],
    "kaizen": ["watchdog", "kaizen_autonomo"]
}

def mapear_identidade(origem):
    for iden, lista in ALIASES.items():
        if origem in lista:
            return iden
    return origem

# ================= IA =================

def gerar_resposta(contexto: str) -> str:
    system_prompt = (
        "Você é o Kaizen, agente autônomo, direto, sarcástico e estratégico"
        " para Nilson Saito. Sempre objetivo e provocador."
    )
    # Gemini
    for model in GEMINI_MODELS:
        try:
            resp = genai.GenerativeModel(model).generate_content([
                {"role": "user", "parts": [f"{system_prompt}\n{contexto}"]}
            ])
            if getattr(resp, "text", None):
                return resp.text.strip()
        except Exception:
            logging.warning(f"Gemini {model} falhou, tentando próximo.")
    # Fallback OpenAI
    try:
        completion = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": contexto}
            ]
        )
        return completion.choices[0].message.content.strip()
    except Exception:
        logging.exception("OpenAI fallback falhou")
        return "Erro geral: todos os modelos falharam."

def gerar_resposta_com_memoria(origem: str, nova_msg: str) -> str:
    memoria    = read_memory()
    identidade = mapear_identidade(origem)
    historico  = [
        m for m in memoria
        if mapear_identidade(m.get("origem")) == identidade
    ][-10:]
    contexto = "\n".join(
        f"Usuário: {m['entrada']}\nKaizen: {m['resposta']}"
        for m in historico
    )
    contexto += f"\nUsuário: {nova_msg}"
    resposta = gerar_resposta(contexto)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "origem": origem,
        "entrada": nova_msg,
        "resposta": resposta
    }
    write_memory(entry)
    return resposta

# ================= AÇÕES =================

def enviar_whatsapp(to: str, msg: str):
    try:
        client_twilio.messages.create(body=msg, from_=FROM_WPP, to=to)
        logging.info(f"[WhatsApp] {to} ← {msg[:50]}...")
    except Exception:
        logging.exception("Falha ao enviar WhatsApp")

# ================= TRELLO =================

def criar_tarefa_trello(titulo: str, descricao: str = "", due_days: int = 1):
    if not all([TRELLO_KEY, TRELLO_TOKEN, TRELLO_LIST_ID]):
        logging.warning("Variáveis Trello não configuradas, tarefa não criada.")
        return None
    due_date = (datetime.now(timezone.utc) + timedelta(days=due_days)) \
                .replace(hour=9, minute=0, second=0, microsecond=0) \
                .isoformat()
    payload = {
        "key":       TRELLO_KEY,
        "token":     TRELLO_TOKEN,
        "idList":    TRELLO_LIST_ID,
        "name":      titulo[:100],
        "desc":      descricao,
        "due":       due_date,
        "pos":       "top"
    }
    try:
        resp = requests.post(f"{TRELLO_API_URL}/cards", params=payload)
        resp.raise_for_status()
        card = resp.json()
        logging.info(f"[Trello] Tarefa criada: {card['id']} → {titulo}")
        return card
    except Exception as e:
        logging.exception(f"Falha ao criar tarefa no Trello: {e}")
        return None

# ================= MONITOR =================

def heartbeat():
    write_memory({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "origem": "sistema",
        "entrada": "heartbeat",
        "resposta": "Kaizen ativo e funcional."
    })

def check_render_service():
    try:
        status = requests.get(
            "https://kaizen-agente.onrender.com/ask",
            timeout=5
        ).status_code
        write_memory({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "origem": "watchdog",
            "entrada": "checar serviço render",
            "resposta": f"status {status}"
        })
    except Exception as e:
        write_memory({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "origem": "watchdog",
            "entrada": "checar serviço render",
            "resposta": f"erro: {e}"
        })

# ================= CÍRCULOS =================

def heartbeat_loop():
    while True:
        heartbeat()
        time.sleep(CHECK_INTERVAL)

def watchdog_loop():
    while True:
        check_render_service()
        time.sleep(WATCHDOG_INTERVAL)

def agendar_relatorio():
    while True:
        now    = datetime.now(CLIENT_TZ)
        target = now.replace(hour=18, minute=0, second=0, microsecond=0)
        if now > target:
            target += timedelta(days=1)
        delay = (target - now).total_seconds()
        logging.info(f"Próximo relatório em {delay/3600:.2f}h")
        time.sleep(delay)
        enviar_whatsapp(TO_WPP, "Kaizen rodando: ✅ Tudo sob controle.")

def pensar_autonomamente():
    hora = datetime.now(CLIENT_TZ).hour
    if 5 <= hora < 9:
        pergunta = "Bom dia. Que atitude proativa você tomaria hoje sem minha intervenção?"
    elif 12 <= hora < 14:
        pergunta = "Hora do almoço. Revise sua performance hoje e gere um insight produtivo."
    elif 18 <= hora < 20:
        pergunta = "Já é fim de expediente. Analise o que aprendeu e o que pode otimizar amanhã."
    else:
        pergunta = "Siga seu julgamento. Execute algo útil com base no histórico."
    try:
        insight = gerar_resposta_com_memoria("saito", pergunta)
        enviar_whatsapp(TO_WPP, insight)
        criar_tarefa_trello(
            titulo = insight.split("\n")[0],
            descricao = insight,
            due_days = 1
        )
    except Exception as e:
        write_memory({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "origem": "kaizen_autonomo",
            "entrada": "erro_ciclo_autonomo",
            "resposta": f"ERRO: {e}"
        })

def ciclo_autonomo_loop():
    while True:
        pensar_autonomamente()
        time.sleep(3600)

# ================= FLASK =================

@app.route('/')
def index():
    return "✅ Kaizen está rodando (Autonomia + Memória unificada + WhatsApp + Trello + Monitor)"

@app.route('/ask', methods=['POST'])
def ask_kaizen_route():
    msg = request.json.get('message')
    if not msg:
        return jsonify({'error': 'mensagem vazia'}), 400
    try:
        reply = gerar_resposta_com_memoria("webhook", msg)
        return jsonify({'reply': reply})
    except Exception as e:
        logging.exception("Erro em /ask")
        return jsonify({'error': str(e)}), 500

@app.route('/whatsapp_webhook', methods=['POST'])
def whatsapp_webhook():
    msg    = request.form.get("Body")
    sender = request.form.get("From")
    try:
        resp = gerar_resposta_com_memoria(sender, msg)
        enviar_whatsapp(sender, resp)
        return "OK", 200
    except Exception as e:
        logging.exception("Erro no webhook WhatsApp")
        return str(e), 500

# ================= BOOT =================

if __name__ == '__main__':
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    threading.Thread(target=watchdog_loop, daemon=True).start()
    threading.Thread(target=agendar_relatorio, daemon=True).start()
    threading.Thread(target=ciclo_autonomo_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
