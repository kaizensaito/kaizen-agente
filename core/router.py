from flask import Flask, request, jsonify
from modules.llm import gerar_resposta_com_memoria, gerar_resposta
from utils.notifications import send_telegram, send_whatsapp

app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "OK", "message": "Kaizen Agente ativo"})

@app.route("/chat", methods=["POST"])
def chat():
    dados = request.json
    prompt = dados.get("prompt")
    if not prompt:
        return jsonify({"error": "Prompt obrigatório"}), 400

    resposta = gerar_resposta_com_memoria(prompt)
    return jsonify({"resposta": resposta})

@app.route("/notify", methods=["POST"])
def notify():
    dados = request.json
    msg = dados.get("message")
    if not msg:
        return jsonify({"error": "Message obrigatório"}), 400

    send_telegram(msg)
    send_whatsapp(msg)
    return jsonify({"status": "notificações enviadas"})
