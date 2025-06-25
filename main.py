import os
import json
import time
import threading
import requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from twilio.rest import Client
import google.generativeai as genai
import openai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
import io
from dotenv import load_dotenv

# Carregar variáveis do .env
load_dotenv()

# Configurações Twilio
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
TO_WPP = os.getenv("TO_WPP") or 'whatsapp:+55XXXXXXXXXXX'  # Seu número WhatsApp
FROM_WPP = 'whatsapp:+14155238886'  # Twilio Sandbox padrão

client_twilio = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Configurações Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)
GEMINI_MODELS = [
    "models/gemini-1.5-flash",
    "models/gemini-1.5-pro",
    "models/gemini-2.5-pro-preview-06-05"
]

# Configuração OpenAI fallback
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

# Google Drive API Config para memória persistente
SCOPES = ['https://www.googleapis.com/auth/drive']
SERVICE_ACCOUNT_FILE = 'credentials.json'
JSON_FILE_NAME = 'kaizen_memory_log.json'

# Intervalos (segundos)
CHECK_INTERVAL = 300  # Heartbeat a cada 5min
WATCHDOG_INTERVAL = 600  # Watchdog a cada 10min

app = Flask(__name__)

##########################
# GOOGLE DRIVE MEMÓRIA
##########################

def drive_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)
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

##########################
# FUNÇÕES DE IA
##########################

def gerar_resposta(mensagem):
    contexto_base = (
        "Você é o Kaizen, um agente de inteligência artificial altamente autônomo, "
        "personalizado para Nilson Saito, que é socorrista e trabalha no resgate rodoviário. Você responde sempre de forma direta, estratégica, sem rodeios, "
        "com foco em melhorar a produtividade, clareza mental, e resultados práticos. "
        "Você age com autonomia total, evita respostas genéricas e não precisa perguntar o que deve fazer. "
        "Seu estilo é objetivo, ousado, provocador, e levemente sarcástico quando necessário. "
        "Fale como um parceiro de elite, não como um assistente."
    )

    for model_name in GEMINI_MODELS:
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content([
                {"role": "user", "parts": [f"{contexto_base}\nUsuário: {mensagem}"]}
            ])
            if hasattr(response, 'text') and response.text:
                return response.text.strip()
        except Exception as e:
            print(f"[Gemini ERRO] {model_name}: {e}")
            continue

    # fallback OpenAI
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
        print("[OpenAI ERRO]", e)
        return "Erro geral: todos os modelos falharam."

def gerar_resposta_com_memoria(usuario, mensagem_nova):
    memoria = read_memory()

    # Filtrar mensagens do usuário para contexto (últimas 10)
    historico_usuario = [m for m in memoria if m.get('origem') == usuario]
    historico_usuario = historico_usuario[-10:]  # últimas 10 interações do usuário

    # Construir contexto
    contexto = (
        "Você é o Kaizen, agente autônomo para Nilson Saito, direto e prático.\n"
        "Mantenha a conversa com base na memória:\n"
    )
    for msg in historico_usuario:
        entrada = msg.get('entrada', '')
        resposta = msg.get('resposta', '')
        contexto += f"Usuário: {entrada}\nKaizen: {resposta}\n"

    contexto += f"Usuário: {mensagem_nova}"

    resposta = gerar_resposta(contexto)

    # Salvar no log (origem = usuário)
    nova_entrada = {
        "timestamp": datetime.utcnow().isoformat() + 'Z',
        "origem": usuario,
        "entrada": mensagem_nova,
        "resposta": resposta
    }
    write_memory(nova_entrada)

    return resposta

##########################
# TWILIO WHATSAPP
##########################

def enviar_whatsapp(to, body):
    try:
        msg = client_twilio.messages.create(
            body=body,
            from_=FROM_WPP,
            to=to
        )
        print(f"[WhatsApp] Enviado para {to}: {body}")
        return msg.sid
    except Exception as e:
        print(f"[WhatsApp ERRO] {e}")
        return None

##########################
# HEARTBEAT & WATCHDOG
##########################

def heartbeat():
    now = datetime.utcnow().isoformat() + 'Z'
    entry = {
        "timestamp": now,
        "origem": "sistema",
        "entrada": "heartbeat",
        "resposta": "Kaizen ativo e funcional."
    }
    write_memory(entry)
    print(f"[{now}] ✅ Heartbeat enviado.")

def heartbeat_loop():
    while True:
        heartbeat()
        time.sleep(CHECK_INTERVAL)

def check_render_service():
    try:
        resp = requests.get("https://kaizen-agente.onrender.com/ask", timeout=5)
        status = resp.status_code
        now = datetime.utcnow().isoformat() + 'Z'
        write_memory({
            "timestamp": now,
            "origem": "watchdog",
            "entrada": "checar serviço render",
            "resposta": f"status {status}"
        })
        print(f"[{now}] 🔍 Render status: {status}")
    except Exception as e:
        now = datetime.utcnow().isoformat() + 'Z'
        write_memory({
            "timestamp": now,
            "origem": "watchdog",
            "entrada": "checar serviço render",
            "resposta": f"erro: {str(e)}"
        })
        print(f"[{now}] ⚠️ Erro ao checar serviço Render: {e}")

def watchdog_loop():
    while True:
        check_render_service()
        time.sleep(WATCHDOG_INTERVAL)

##########################
# RELATÓRIO DIÁRIO VIA WHATSAPP
##########################

def enviar_relatorio_diario():
    try:
        msg = "Kaizen rodando: ✅\nMensagens respondidas hoje: XX\nErros: 0\nTudo funcionando liso."
        enviar_whatsapp(TO_WPP, msg)
        print("[Relatório] Enviado com sucesso")
    except Exception as e:
        print(f"[Relatório ERRO] {e}")

def agendar_relatorio():
    while True:
        agora = datetime.now()
        proximo_envio = agora.replace(hour=18, minute=0, second=0, microsecond=0)
        if agora > proximo_envio:
            proximo_envio += timedelta(days=1)
        tempo_espera = (proximo_envio - agora).total_seconds()
        threading.Timer(tempo_espera, enviar_relatorio_diario).start()
        threading.Event().wait(tempo_espera + 1)

##########################
# ROTAS FLASK
##########################

@app.route('/')
def index():
    return "✅ Kaizen está rodando (Twilio + Gemini + OpenAI + Memória Drive)"

@app.route('/send_whatsapp', methods=['POST'])
def send_whatsapp_route():
    try:
        msg = request.json.get('message', 'Mensagem teste funcionando!')
        sid = enviar_whatsapp(TO_WPP, msg)
        return jsonify({'status': 'success', 'sid': sid})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/ask', methods=['POST'])
def ask_kaizen_route():
    try:
        user_input = request.json.get('message')
        if not user_input:
            return jsonify({'error': 'Mensagem vazia'}), 400
        reply = gerar_resposta_com_memoria(TO_WPP, user_input)
        return jsonify({'reply': reply})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/whatsapp_webhook', methods=['POST'])
def whatsapp_webhook_route():
    try:
        incoming_msg = request.form.get("Body")
        sender = request.form.get("From")
        print(f"[WhatsApp] De: {sender} | Msg: {incoming_msg}")

        if not incoming_msg:
            return "Mensagem vazia", 400

        reply = gerar_resposta_com_memoria(sender, incoming_msg)

        enviar_whatsapp(sender, reply)

        return "OK", 200

    except Exception as e:
        print(f"[Webhook ERRO] {e}")
        return str(e), 500

##########################
# EXECUÇÃO
##########################

if __name__ == '__main__':
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    threading.Thread(target=watchdog_loop, daemon=True).start()
    threading.Thread(target=agendar_relatorio, daemon=True).start()

    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
