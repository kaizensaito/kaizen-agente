from flask import Flask, request, jsonify
from utils.notifications import send_telegram, send_whatsapp
from modules.llm import gerar_resposta_com_memoria, gerar_resposta

app = Flask(__name__)

@app.route('/telegram_webhook', methods=['POST'])
def telegram_webhook():
    data = request.json
    print("Telegram webhook recebido:", data)
    # Aqui você pode chamar a LLM para processar a mensagem e responder
    resposta = gerar_resposta(data)
    # Exemplo: enviar resposta via Telegram (implemente send_telegram)
    chat_id = data.get("message", {}).get("chat", {}).get("id")
    if chat_id and resposta:
        send_telegram(chat_id, resposta)
    return jsonify({"status": "ok"})

@app.route('/whatsapp_webhook', methods=['POST'])
def whatsapp_webhook():
    data = request.json
    print("WhatsApp webhook recebido:", data)
    resposta = gerar_resposta(data)
    from_number = data.get("From")
    if from_number and resposta:
        send_whatsapp(from_number, resposta)
    return jsonify({"status": "ok"})

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "alive"})



