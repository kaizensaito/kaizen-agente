import os
import logging
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from twilio.rest import Client
from dotenv import load_dotenv

load_dotenv()

# ⚙️ ENV VARS
TELEGRAM_TOKEN     = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN  = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM        = os.getenv("TWILIO_FROM_NUMBER")
TWILIO_TO          = os.getenv("TWILIO_TO_NUMBER")
GMAIL_USER         = os.getenv("GMAIL_USER")
GMAIL_PASS         = os.getenv("GMAIL_PASS")

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

def send_telegram(chat_id, text):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text}
        )
        if not r.ok:
            logging.error(f"[telegram] {r.status_code}: {r.text}")
    except Exception:
        logging.exception("[telegram] erro")

def send_whatsapp(msg):
    try:
        twilio_client.messages.create(
            body=msg,
            from_=f"whatsapp:{TWILIO_FROM}",
            to=f"whatsapp:{TWILIO_TO}"
        )
        logging.info("[whatsapp] enviado")
    except Exception as e:
        logging.error(f"[whatsapp] erro: {e}")

def send_email(subject, body):
    try:
        msg = MIMEMultipart()
        msg['From']    = msg['To'] = GMAIL_USER
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.send_message(msg)
        logging.info("[email] enviado")
    except Exception as e:
        logging.error(f"[email] erro: {e}")
