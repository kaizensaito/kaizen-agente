import requests

# Token Telegram EXPOSITO direto na cara
TELEGRAM_BOT_TOKEN = "7968219889:AAE0QsMpWwkVtHAY9mdsCp35vU3hqkmukOQ"

# Configuracão hipotética WhatsApp - ajusta conforme seu serviço real
WHATSAPP_API_URL = "https://api.whatsapp.example/send"
WHATSAPP_API_TOKEN = "token_whatsapp_supersecreto"

def send_telegram(chat_id, mensagem):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": mensagem}
    try:
        resp = requests.post(url, json=payload)
        resp.raise_for_status()
        print(f"Telegram enviado para {chat_id}")
    except Exception as e:
        print(f"Erro no Telegram: {e}")

def send_whatsapp(numero, mensagem):
    url = WHATSAPP_API_URL
    headers = {
        "Authorization": f"Bearer {WHATSAPP_API_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "to": numero,
        "type": "text",
        "text": {"body": mensagem}
    }
    try:
        resp = requests.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        print(f"WhatsApp enviado para {numero}")
    except Exception as e:
        print(f"Erro no WhatsApp: {e}")
import requests

TELEGRAM_BOT_TOKEN = "SEU_TELEGRAM_TOKEN_AQUI"
WHATSAPP_API_URL = "URL_DA_API_WHATSAPP"
WHATSAPP_API_TOKEN = "TOKEN_WHATSAPP"

def send_telegram(chat_id, mensagem):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": mensagem}
    try:
        resp = requests.post(url, json=payload)
        resp.raise_for_status()
        print(f"Telegram enviado para {chat_id}")
    except Exception as e:
        print(f"Erro ao enviar Telegram: {e}")

def send_whatsapp(numero, mensagem):
    url = WHATSAPP_API_URL
    headers = {"Authorization": f"Bearer {WHATSAPP_API_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "to": numero,
        "type": "text",
        "text": {"body": mensagem}
    }
    try:
        resp = requests.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        print(f"WhatsApp enviado para {numero}")
    except Exception as e:
        print(f"Erro ao enviar WhatsApp: {e}")
