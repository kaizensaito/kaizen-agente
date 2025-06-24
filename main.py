from flask import Flask, request
from datetime import datetime, timedelta
import os
import pytz
import logging
import openai
from google.oauth2 import service_account
from googleapiclient.discovery import build

# === CONFIGURAÇÕES ===
openai.api_key = os.getenv("OPENAI_API_KEY")  # Deve estar definida no Render

# Google Calendar
SCOPES = ['https://www.googleapis.com/auth/calendar']
SERVICE_ACCOUNT_FILE = 'credentials.json'
CALENDAR_ID = 'nilson.saito@gmail.com'  # Agenda certa agora

# Timezone Brasil
TIMEZONE = 'America/Sao_Paulo'

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)


# === Funções ===

def usar_chatgpt(prompt):
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        return response['choices'][0]['message']['content']
    except Exception as e:
        logging.error(f"[GPT ERROR] {e}")
        return "Erro com o ChatGPT"

def criar_evento_google(titulo, data, hora_inicio, hora_fim):
    try:
        credentials = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
        service = build('calendar', 'v3', credentials=credentials)

        inicio = datetime.strptime(f"{data} {hora_inicio}", "%Y-%m-%d %H:%M")
        fim = datetime.strptime(f"{data} {hora_fim}", "%Y-%m-%d %H:%M")

        evento = {
            'summary': titulo,
            'start': {'dateTime': inicio.isoformat(), 'timeZone': TIMEZONE},
            'end': {'dateTime': fim.isoformat(), 'timeZone': TIMEZONE},
        }

        evento = service.events().insert(calendarId=CALENDAR_ID, body=evento).execute()
        logging.info(f"[CALENDAR] Evento criado: {evento.get('htmlLink')}")
        return evento.get('htmlLink')
    except Exception as e:
        logging.error(f"[CALENDAR ERROR] {e}")
        return None

@app.route('/criar_evento', methods=['POST'])
def criar_evento():
    dados = request.json
    titulo = dados.get('titulo', 'Evento')
    data = dados.get('data')
    hora_inicio = dados.get('hora_inicio')
    hora_fim = dados.get('hora_fim')

    if not all([data, hora_inicio, hora_fim]):
        return {"erro": "Dados incompletos"}, 400

    link = criar_evento_google(titulo, data, hora_inicio, hora_fim)
    if link:
        return {"mensagem": "Evento criado com sucesso", "link": link}
    else:
        return {"erro": "Falha ao criar evento"}, 500

@app.route('/')
def index():
    return "Kaizen operacional com GPT-3.5 turbo e acesso à agenda Nilson."


# === Execução local
if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=10000)
