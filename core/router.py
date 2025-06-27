from flask import Flask, request, jsonify
from modules.llm import gerar_resposta_com_memoria, gerar_resposta
from utils.notifications import send_telegram, send_whatsapp

app = Flask(__name__)

@app.route("/")
def home():
    return "Kaizen Agente está no ar."

@app.route("/telegram_webhook", methods=["POST"])
def telegram_webhook():
    data = request.json
    if not data or "message" not in data:
        return jsonify({"status": "no message"}), 400
    
    chat_id = data["message"]["chat"]["id"]
    texto = data["message"].get("text", "")

    resposta = gerar_resposta(texto)
    send_telegram(chat_id, resposta)
    return jsonify({"status": "ok"})

@app.route("/whatsapp_webhook", methods=["POST"])
def whatsapp_webhook():
    data = request.json
    numero = data.get("from")
    texto = data.get("text", {}).get("body", "")

    resposta = gerar_resposta(texto)
    send_whatsapp(numero, resposta)
    return jsonify({"status": "ok"})

# Se precisar de mais rotas, põe aqui sem medo.

