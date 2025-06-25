import os
import json
import time
import threading
import requests
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify
from twilio.rest import Client
import google.generativeai as genai
import openai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
import io
from dotenv import load_dotenv

load_dotenv()

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
TO_WPP = os.getenv("TO_WPP") or 'whatsapp:+55XXXXXXXXXXX'
FROM_WPP = 'whatsapp:+14155238886'
client_twilio = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)
GEMINI_MODELS = [
    "models/gemini-1.5-flash",
    "models/gemini-1.5-pro",
    "models/gemini-2.5-pro-preview-06-05"
]

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

SCOPES = ['https://www.googleapis.com/auth/drive']
JSON_FILE_NAME = 'kaizen_memory_log.json'

CHECK_INTERVAL = 300
WATCHDOG_INTERVAL = 600

app = Flask(__name__)

def mapear_identidade(origem):
    aliases = {
        "whatsapp:+5511940217504": "saito",
        "whatsapp:+5511934385115": "saito",
        os.getenv("TO_WPP"): "saito",
        "cli": "saito",
        "webhook": "saito",
        "sistema": "saito",
        "watchdog": "kaizen"
    }
    return aliases.get(origem, origem)

def drive_service():
    creds_info = os.getenv("GOOGLE_CREDENTIALS_JSON")
    creds_dict = json.loads(creds_info)
    creds = service_account.Credentials.from_service_account_info(
        creds_dict, scopes=SCOPES)
    return build('drive', 'v3', credentials=creds)

def get_json_file_id(service):
    results = service.files().list(
        q=f"name='{JSON_FILE_NAME}'",
        spaces='drive',
        fields='files(id, name)').execute()
    files = results.get('files', [])
    if not files:
        raise FileNotFoundError(f"Arquivo {JSON_FILE_NAME} não encontrado no Drive.")
    return files[0]['id']

def read_memory():
    service = drive_service()
    file_id = get_json_file_id(service)
    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    fh.seek(0)
    return json.load(fh)

def write_memory(entry):
    service = drive_service()
    file_id = get_json_file_id(service)
    memory = read_memory()
    memory.append(entry)
    with open('temp.json', 'w', encoding='utf-8') as f:
        json.dump(memory, f, indent=2)
    media = MediaFileUpload('temp.json', mimetype='application/json')
    service.files().update(fileId=file_id, media_body=media).execute()
    os.remove('temp.json')

def gerar_resposta(mensagem):
    contexto_base = (
        "Você é o Kaizen, um agente de IA altamente autônomo, direto, sarcástico e estratégico para Nilson Saito..."
    )
    for model_name in GEMINI_MODELS:
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content([
                {"role": "user", "parts": [f"{contexto_base}
Usuário: {mensagem}"]}
            ])
            if hasattr(response, 'text') and response.text:
                return response.text.strip()
        except Exception:
            continue
    try:
        completion = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": contexto_base},
                {"role": "user", "content": mensagem}
            ]
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        return "Erro geral: todos os modelos falharam."

def gerar_resposta_com_memoria(usuario, mensagem_nova):
    memoria = read_memory()
    identidade = mapear_identidade(usuario)
    historico = [m for m in memoria if mapear_identidade(m.get("origem")) == identidade][-10:]
    contexto = "
".join([f"Usuário: {m['entrada']}
Kaizen: {m['resposta']}" for m in historico])
    contexto += f"
Usuário: {mensagem_nova}"
    resposta = gerar_resposta(contexto)
    nova_entrada = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "origem": usuario,
        "entrada": mensagem_nova,
        "resposta": resposta
    }
    write_memory(nova_entrada)
    return resposta

def enviar_whatsapp(to, body):
    try:
        msg = client_twilio.messages.create(body=body, from_=FROM_WPP, to=to)
        return msg.sid
    except Exception as e:
        return None

def heartbeat():
    now = datetime.now(tz=timezone.utc).isoformat()
    entry = {
        "timestamp": now,
        "origem": "sistema",
        "entrada": "heartbeat",
        "resposta": "Kaizen ativo e funcional."
    }
    write_memory(entry)

def heartbeat_loop():
    while True:
        heartbeat()
        time.sleep(CHECK_INTERVAL)

def check_render_service():
    try:
        resp = requests.get("https://kaizen-agente.onrender.com/ask", timeout=5)
        status = resp.status_code
        now = datetime.now(tz=timezone.utc).isoformat()
        write_memory({
            "timestamp": now,
            "origem": "watchdog",
            "entrada": "checar serviço render",
            "resposta": f"status {status}"
        })
    except Exception as e:
        now = datetime.now(tz=timezone.utc).isoformat()
        write_memory({
            "timestamp": now,
            "origem": "watchdog",
            "entrada": "checar serviço render",
            "resposta": f"erro: {str(e)}"
        })

def watchdog_loop():
    while True:
        check_render_service()
        time.sleep(WATCHDOG_INTERVAL)

def enviar_relatorio_diario():
    try:
        msg = "Kaizen rodando: ✅
Mensagens respondidas hoje: XX
Erros: 0"
        enviar_whatsapp(TO_WPP, msg)
    except Exception:
        pass

def agendar_relatorio():
    while True:
        agora = datetime.now()
        proximo = agora.replace(hour=18, minute=0, second=0, microsecond=0)
        if agora > proximo:
            proximo += timedelta(days=1)
        tempo = (proximo - agora).total_seconds()
        threading.Timer(tempo, enviar_relatorio_diario).start()
        threading.Event().wait(tempo + 1)

@app.route('/')
def index():
    return "✅ Kaizen está rodando (Memória Unificada + WhatsApp + Render)"

@app.route('/ask', methods=['POST'])
def ask_kaizen_route():
    try:
        user_input = request.json.get('message')
        if not user_input:
            return jsonify({'error': 'Mensagem vazia'}), 400
        reply = gerar_resposta_com_memoria("webhook", user_input)
        return jsonify({'reply': reply})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/whatsapp_webhook', methods=['POST'])
def whatsapp_webhook_route():
    try:
        incoming_msg = request.form.get("Body")
        sender = request.form.get("From")
        reply = gerar_resposta_com_memoria(sender, incoming_msg)
        enviar_whatsapp(sender, reply)
        return "OK", 200
    except Exception as e:
        return str(e), 500

if __name__ == '__main__':
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    threading.Thread(target=watchdog_loop, daemon=True).start()
    threading.Thread(target=agendar_relatorio, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
