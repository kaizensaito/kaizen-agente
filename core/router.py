from flask import Flask, request, jsonify
import os
import requests

app = Flask(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

def send_telegram(chat_id, text):
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        resp = requests.post(url, json=payload)
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"Erro ao enviar Telegram: {e}")
        return False

@app.route("/telegram_webhook", methods=["POST"])
def telegram_webhook():
    data = request.json
    if not data or "message" not in data:
        return jsonify({"ok": True})

    chat_id = data["message"]["chat"]["id"]
    text = data["message"].get("text", "")
    
    # Responde com o mesmo texto recebido, pode mudar para lógica real
    reply = f"Recebi sua mensagem: {text}"
    send_telegram(chat_id, reply)
    
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
