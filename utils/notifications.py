import requests
import os

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

def send_telegram(chat_id: str, mensagem: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": mensagem
    }

    try:
        response = requests.post(url, json=payload)
        print(f"[🚀 Enviando mensagem para {chat_id}]")
        print(f"[📦 Payload] {payload}")
        print(f"[📡 Status Telegram] {response.status_code} - {response.text}")

        if not response.ok:
            print(f"[❌ Erro ao enviar Telegram] {response.text}")

    except Exception as e:
        print(f"[🔥 EXCEÇÃO Telegram] {str(e)}")
