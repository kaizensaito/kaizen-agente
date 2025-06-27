import os, io, json, time, threading, logging, requests, schedule, smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

# ========== CONFIGURAÇÕES ==========
EMAIL_ORIGEM = os.getenv("EMAIL_ORIGEM")
EMAIL_DESTINO = os.getenv("EMAIL_DESTINO")
EMAIL_SENHA = os.getenv("EMAIL_SENHA")

WHATSAPP_API_URL = os.getenv("WHATSAPP_API_URL")  # Endpoint do Twilio ou similar
WHATSAPP_NUMERO = os.getenv("WHATSAPP_NUMERO")

TRELLO_KEY = os.getenv("TRELLO_KEY")
TRELLO_TOKEN = os.getenv("TRELLO_TOKEN")
TRELLO_BOARD_ID = os.getenv("TRELLO_BOARD_ID")
TRELLO_LIST_ID = os.getenv("TRELLO_LIST_ID")

TIMEZONE = ZoneInfo("America/Sao_Paulo")

# ========== LOG ==========
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ========== FUNÇÕES ==========
def enviar_email(assunto, corpo):
    msg = MIMEMultipart()
    msg['From'] = EMAIL_ORIGEM
    msg['To'] = EMAIL_DESTINO
    msg['Subject'] = assunto
    msg.attach(MIMEText(corpo, 'plain'))

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(EMAIL_ORIGEM, EMAIL_SENHA)
            server.send_message(msg)
            logging.info("Email enviado com sucesso.")
    except Exception as e:
        logging.error(f"Erro ao enviar e-mail: {e}")

def enviar_whatsapp(mensagem):
    payload = {
        "to": WHATSAPP_NUMERO,
        "message": mensagem
    }
    try:
        response = requests.post(WHATSAPP_API_URL, json=payload)
        response.raise_for_status()
        logging.info("Mensagem enviada via WhatsApp.")
    except Exception as e:
        logging.error(f"Erro ao enviar WhatsApp: {e}")

def criar_card_trello(titulo, descricao=""):
    url = f"https://api.trello.com/1/cards"
    params = {
        "name": titulo,
        "desc": descricao,
        "idList": TRELLO_LIST_ID,
        "key": TRELLO_KEY,
        "token": TRELLO_TOKEN
    }
    try:
        response = requests.post(url, params=params)
        response.raise_for_status()
        logging.info("Card criado no Trello.")
    except Exception as e:
        logging.error(f"Erro ao criar card Trello: {e}")

# ========== HEARTBEAT ==========
def heartbeat():
    agora = datetime.now(TIMEZONE)
    msg = f"🧠 Kaizen ativo\n🕒 Horário: {agora.strftime('%d/%m/%Y %H:%M:%S')}"
    logging.info(msg)
    enviar_email("✅ Heartbeat Kaizen", msg)
    enviar_whatsapp(msg)

# ========== LOOP PRINCIPAL ==========
def loop_monitoramento():
    schedule.every(3).hours.do(heartbeat)  # A cada 3 horas

    while True:
        try:
            schedule.run_pending()
            time.sleep(10)
        except Exception as e:
            logging.error(f"Erro no loop principal: {e}")
            time.sleep(30)

# ========== THREAD ==========
if __name__ == "__main__":
    logging.info("Kaizen iniciado com sucesso.")
    threading.Thread(target=loop_monitoramento).start()
