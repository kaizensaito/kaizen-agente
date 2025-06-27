import os
import traceback
from flask import Flask, request, jsonify

app = Flask(__name__)

# Variáveis ambiente
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

@app.route('/')
def index():
    return "Kaizen agente ativo!"

@app.route('/telegram_webhook', methods=['POST'])
def telegram_webhook():
    try:
        data = request.get_json(force=True)
        print("Recebido Telegram:", data)

        if "message" in data and "text" in data["message"]:
            user_text = data["message"]["text"]
            chat_id = data["message"]["chat"]["id"]

            # Aqui você pode processar o texto e gerar resposta automática
            resposta = f"Memória atualizada. Resposta para: {user_text}"

            # Manda a resposta de volta para Telegram (exemplo simples)
            import requests
            send_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            payload = {"chat_id": chat_id, "text": resposta}
            r = requests.post(send_url, json=payload)
            print("Resposta enviada, status:", r.status_code)

        return jsonify({"status": "ok"})

    except Exception as e:
        print("Erro no webhook Telegram:", e)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# Outras rotas e blueprints aqui, se precisar

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
