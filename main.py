
import os
import io
import json
import time
import threading
import logging
import requests
import smtplib
import schedule

from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from zoneinfo import ZoneInfo

import openai
import google.generativeai as genai

# ==== CONFIGURAÇÕES GERAIS ====
TZ = ZoneInfo("America/Sao_Paulo")
KAIZEN_EMAIL = "kaizen.saito.ai@gmail.com"
SAITO_EMAIL = "nilson.saito@gmail.com"
WHATSAPP_API_URL = "https://api.twilio.com"  # placeholder, não usamos diretamente aqui
HEARTBEAT_HORA = "18:00"
TRELLO_API_KEY = os.getenv("TRELLO_API_KEY", "")
TRELLO_TOKEN = os.getenv("TRELLO_TOKEN", "")
TRELLO_BOARD_ID = os.getenv("TRELLO_BOARD_ID", "")
TRELLO_LIST_ID = os.getenv("TRELLO_LIST_ID", "")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# ==== LOGGING ====
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ==== HEARTBEAT ====
def enviar_heartbeat():
    logging.info("Enviando heartbeat por e-mail...")
    try:
        msg = MIMEMultipart()
        msg["From"] = KAIZEN_EMAIL
        msg["To"] = SAITO_EMAIL
        msg["Subject"] = "KAIZEN - Heartbeat Diário"

        corpo = f"Status OK às {datetime.now(TZ).strftime('%d/%m/%Y %H:%M:%S')}."
        msg.attach(MIMEText(corpo, "plain"))

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(KAIZEN_EMAIL, os.getenv("KAIZEN_EMAIL_PASSWORD"))
            server.sendmail(KAIZEN_EMAIL, SAITO_EMAIL, msg.as_string())
        logging.info("E-mail enviado com sucesso.")
    except Exception as e:
        logging.error(f"Erro ao enviar heartbeat: {e}")

# ==== TRELLO ====
def criar_tarefa_trello(titulo, descricao):
    logging.info("Criando tarefa no Trello...")
    url = f"https://api.trello.com/1/cards"
    query = {
        "key": TRELLO_API_KEY,
        "token": TRELLO_TOKEN,
        "idList": TRELLO_LIST_ID,
        "name": titulo,
        "desc": descricao
    }
    try:
        r = requests.post(url, params=query)
        r.raise_for_status()
        logging.info("Tarefa criada com sucesso.")
    except Exception as e:
        logging.error(f"Erro ao criar tarefa no Trello: {e}")

# ==== MONITORAMENTO ====
def rotina_checagem():
    logging.info("Executando rotina de checagem.")
    try:
        # Aqui vai o que estiver rodando no core
        criar_tarefa_trello("Check Kaizen", f"Status verificado às {datetime.now(TZ)}")
    except Exception as e:
        logging.error(f"Erro na rotina de checagem: {e}")

# ==== LOOP DE AÇÕES PROGRAMADAS ====
def iniciar_agendamentos():
    logging.info("Iniciando agendamentos.")
    schedule.every().day.at(HEARTBEAT_HORA).do(enviar_heartbeat)
    schedule.every(1).hours.do(rotina_checagem)

    while True:
        schedule.run_pending()
        time.sleep(10)

# ==== THREAD ====
if __name__ == "__main__":
    logging.info("KAIZEN MAIN INICIADO")
    t = threading.Thread(target=iniciar_agendamentos)
    t.start()
