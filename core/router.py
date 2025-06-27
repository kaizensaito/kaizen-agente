from flask import Flask, request, jsonify

app = Flask(__name__)

TELEGRAM_TOKEN = "7968219889:AAE0QsMpWwkVtHAY9mdsCp35vU3hqkmukOQ"
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

@app.route('/telegram_webhook', methods=['POST'])
def telegram_webhook():
    data = request.get_json()
    if not data:
        return jsonify({"status": "no data"}), 400

    # Processa mensagem do Telegram
    if 'message' in data:
        chat_id = data['message']['chat']['id']
        text = data['message'].get('text', '')

        # Resposta automática simples
        resposta = f"Recebi sua mensagem: {text}"

        import requests
        send_url = f"{TELEGRAM_API_URL}/sendMessage"
        payload = {"chat_id": chat_id, "text": resposta}
        requests.post(send_url, json=payload)

    return jsonify({"status": "ok"}), 200


@app.route('/')
def index():
    return "Kaizen Agent API is up."


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
