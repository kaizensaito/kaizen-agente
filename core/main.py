import os
import io
import json
import time
import threading
import logging
import requests
import schedule
import smtplib
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup
import google.generativeai as genai

# Configuração básica de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# Carregando variáveis de ambiente
load_dotenv()

# Constantes e variáveis globais
CLIENT_TZ = ZoneInfo("America/Sao_Paulo")

EMAIL_DESTINO = os.getenv("EMAIL_DESTINO")
EMAIL_ORIGEM = os.getenv("EMAIL_ORIGEM")
EMAIL_SENHA = os.getenv("EMAIL_SENHA")

FROM_WPP = os.getenv("FROM_WPP")
TO_WPP = os.getenv("TO_WPP")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

HUGGINGFACE_API_TOKEN = os.getenv("HUGGINGFACE_API_TOKEN")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_API_KEY_MAIN = os.getenv("OPENAI_API_KEY_MAIN")
OPENAI_API_KEY_OPTIMIZER = os.getenv("OPENAI_API_KEY_OPTIMIZER")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

TRELLO_BOARD_ID = os.getenv("TRELLO_BOARD_ID")
TRELLO_LIST_ID = os.getenv("TRELLO_LIST_ID")
TRELLO_KEY = os.getenv("TRELLO_KEY")
TRELLO_TOKEN = os.getenv("TRELLO_TOKEN")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")

ML_CLIENT_ID = os.getenv("ML_CLIENT_ID")
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET")

RENDER_API_KEY = os.getenv("RENDER_API_KEY")
SERVICE_ID = os.getenv("service_id")

# Inicialização do Flask
app = Flask(__name__)

# Configurações do Google Generative AI
genai.configure(api_key=GEMINI_API_KEY)

# Funções auxiliares e principais começam aqui

def send_email(subject: str, body: str):
    """Envia email via SMTP usando as credenciais configuradas."""
    try:
        logging.info("Iniciando envio de e-mail...")
        msg = MIMEMultipart()
        msg['From'] = EMAIL_ORIGEM
        msg['To'] = EMAIL_DESTINO
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(EMAIL_ORIGEM, EMAIL_SENHA)
        server.send_message(msg)
        server.quit()
        logging.info("Email enviado com sucesso.")
    except Exception as e:
        logging.error(f"Erro ao enviar email: {e}")

def send_whatsapp_message(message: str):
    """Envia mensagem WhatsApp via Twilio."""
    from twilio.rest import Client
    try:
        logging.info("Enviando mensagem WhatsApp...")
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            body=message,
            from_=FROM_WPP,
            to=TO_WPP
        )
        logging.info(f"Mensagem WhatsApp enviada: SID {message.sid}")
    except Exception as e:
        logging.error(f"Erro ao enviar mensagem WhatsApp: {e}")

# Continue me dizendo quando quiser que siga com mais sem perder nada e sem cortar funções.

# Função para integração com Gemini API (Google Generative AI)
def call_gemini_api(prompt: str):
    try:
        logging.info("[gemini] chamando API...")
        response = genai.generate_text(model="gemini-1.5-flash", prompt=prompt)
        return response.text
    except Exception as e:
        logging.warning(f"gemini falhou: {e}")
        raise

# Função para integração com HuggingFace Mistral (fallback)
def call_mistral_api(prompt: str):
    url = "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.1"
    headers = {"Authorization": f"Bearer {HUGGINGFACE_API_TOKEN}"}
    data = {"inputs": prompt}
    try:
        logging.info("[mistral] chamando API...")
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        output = response.json()
        return output[0]['generated_text'] if output else ""
    except Exception as e:
        logging.warning(f"mistral falhou: {e}")
        raise

# Função para integração com OpenRouter
def call_openrouter_api(prompt: str):
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "openai/gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}]
    }
    try:
        logging.info("[openrouter] chamando API...")
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        output = response.json()
        return output['choices'][0]['message']['content']
    except Exception as e:
        logging.warning(f"openrouter falhou: {e}")
        raise

# Função para integração com OpenAI GPT (fallback final)
def call_openai_api(prompt: str, model="gpt-3.5-turbo"):
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    url = "https://api.openai.com/v1/chat/completions"
    data = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1000,
        "temperature": 0.7
    }
    try:
        logging.info(f"[openai:{model}] chamando API...")
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        output = response.json()
        return output['choices'][0]['message']['content']
    except Exception as e:
        logging.warning(f"openai {model} falhou: {e}")
        raise

# Função de fallback principal que tenta em sequência todas APIs
def generate_response_with_fallback(prompt: str):
    try:
        return call_gemini_api(prompt)
    except:
        pass
    try:
        return call_mistral_api(prompt)
    except:
        pass
    try:
        return call_openrouter_api(prompt)
    except:
        pass
    try:
        return call_openai_api(prompt, model="gpt-3.5-turbo")
    except:
        pass
    try:
        return call_openai_api(prompt, model="gpt-4o")
    except Exception as e:
        logging.error(f"Todas APIs falharam: {e}")
        return "Desculpe, não consegui processar sua solicitação no momento."

# Rotas básicas da API RESTful do Flask
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "ok", "timestamp": datetime.now(CLIENT_TZ).isoformat()})

# Endpoint para gerar resposta via prompt recebido
@app.route('/generate', methods=['POST'])
def generate_endpoint():
    data = request.json
    prompt = data.get("prompt", "")
    if not prompt:
        return jsonify({"error": "Prompt é obrigatório"}), 400
    logging.info(f"Gerando resposta para prompt: {prompt[:50]}...")
    response = generate_response_with_fallback(prompt)
    return jsonify({"response": response})

# Outros endpoints para controle, comandos, integração, etc.
# Função para enviar mensagem via Twilio WhatsApp
def send_whatsapp_message(body: str):
    from twilio.rest import Client
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    try:
        message = client.messages.create(
            body=body,
            from_=FROM_WPP,
            to=TO_WPP
        )
        logging.info(f"Mensagem WhatsApp enviada: SID {message.sid}")
        return True
    except Exception as e:
        logging.error(f"Erro ao enviar WhatsApp: {e}")
        return False

# Função para enviar email usando SMTP
def send_email(subject: str, body: str):
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    msg = MIMEMultipart()
    msg['From'] = EMAIL_ORIGEM
    msg['To'] = EMAIL_DESTINO
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    try:
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(EMAIL_ORIGEM, EMAIL_SENHA)
            server.sendmail(EMAIL_ORIGEM, EMAIL_DESTINO, msg.as_string())
        logging.info("Email enviado com sucesso")
        return True
    except Exception as e:
        logging.error(f"Erro ao enviar email: {e}")
        return False

# Função para integração com Google Calendar (inserir evento)
def inserir_evento_google_calendar(summary, description, start_datetime, end_datetime):
    try:
        service = criar_servico_calendar()
        event = {
            'summary': summary,
            'description': description,
            'start': {'dateTime': start_datetime.isoformat(), 'timeZone': str(CLIENT_TZ)},
            'end': {'dateTime': end_datetime.isoformat(), 'timeZone': str(CLIENT_TZ)},
        }
        evento = service.events().insert(calendarId=GOOGLE_CALENDAR_ID, body=event).execute()
        logging.info(f"Evento inserido no Google Calendar: {evento.get('htmlLink')}")
        return evento.get('htmlLink')
    except Exception as e:
        logging.error(f"Erro ao inserir evento no Google Calendar: {e}")
        return None

# Função para criar serviço Google Calendar
def criar_servico_calendar():
    creds = get_creds()
    if not creds:
        raise Exception("Credenciais inválidas para Google Calendar.")
    service = build('calendar', 'v3', credentials=creds)
    return service

# Função para reiniciar contadores diários, rodar a cada 24h
def reset_daily_counters():
    global daily_api_calls
    while True:
        daily_api_calls = 0
        logging.info("Contadores diários reiniciados.")
        time.sleep(86400)  # 24 horas

# Loop de agendamento para jobs periódicos
def schedule_loop():
    while True:
        schedule.run_pending()
        time.sleep(1)

# Função principal autônoma rodando em thread
def autonomous_loop():
    while True:
        # Exemplo de tarefa periódica: enviar heartbeat ou status
        logging.info("Heartbeat ativo - sistema funcionando.")
        time.sleep(3600)  # a cada 1h
# Endpoint Flask para receber comandos externos via webhook
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    logging.info(f"Webhook recebido: {data}")

    comando = data.get('command')
    if not comando:
        return jsonify({"error": "Comando não informado"}), 400

    if comando == "enviar_email":
        assunto = data.get('subject', 'Sem assunto')
        corpo = data.get('body', '')
        sucesso = send_email(assunto, corpo)
        return jsonify({"status": "sucesso" if sucesso else "falha"})

    elif comando == "enviar_whatsapp":
        mensagem = data.get('message', '')
        sucesso = send_whatsapp_message(mensagem)
        return jsonify({"status": "sucesso" if sucesso else "falha"})

    elif comando == "inserir_evento_calendar":
        summary = data.get('summary')
        description = data.get('description')
        start_str = data.get('start_datetime')
        end_str = data.get('end_datetime')

        if not all([summary, start_str, end_str]):
            return jsonify({"error": "Dados incompletos para evento"}), 400

        from datetime import datetime
        start_dt = datetime.fromisoformat(start_str)
        end_dt = datetime.fromisoformat(end_str)
        link_evento = inserir_evento_google_calendar(summary, description, start_dt, end_dt)
        if link_evento:
            return jsonify({"status": "sucesso", "link": link_evento})
        else:
            return jsonify({"status": "falha"}), 500

    else:
        return jsonify({"error": "Comando desconhecido"}), 400

# Endpoint para status básico
@app.route('/status', methods=['GET'])
def status():
    return jsonify({"status": "online", "timestamp": datetime.now().isoformat()})

# Inicialização dos loops autônomos e servidor Flask
if __name__ == "__main__":
    # Inicia loops autônomos em threads separadas
    threading.Thread(target=autonomous_loop, daemon=True).start()
    threading.Thread(target=reset_daily_counters, daemon=True).start()
    threading.Thread(target=schedule_loop, daemon=True).start()

    # Roda servidor Flask
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
