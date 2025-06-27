from flask import Blueprint, request, jsonify
from modules.llm import gerar_resposta_com_memoria
from utils.notifications import send_telegram

router = Blueprint('router', __name__)

@router.route("/", methods=["GET"])
def index():
    return "✅ Kaizen online"

@router.route("/status", methods=["GET"])
def status():
    return jsonify({"status": "ok"})

@router.route("/ask", methods=["POST"])
def ask():
    data = request.get_json()
    pergunta = data.get("pergunta")
    if not pergunta:
        return jsonify({"erro": "Pergunta não fornecida"}), 400
    resposta = gerar_resposta_com_memoria(pergunta)
    return jsonify({"resposta": resposta})

@router.route("/telegram_webhook", methods=["POST"])
def telegram_webhook():
    data = request.get_json()
    if not data:
        return "Sem dados", 400

    try:
        mensagem = data["message"]["text"]
        chat_id = data["message"]["chat"]["id"]
        print(f"[📩 Telegram recebido] {mensagem}")

        resposta = gerar_resposta_com_memoria(mensagem)
        print(f"[🤖 Resposta gerada] {resposta}")

        send_telegram(chat_id, resposta)
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"[❌ ERRO webhook] {str(e)}")
        return jsonify({"erro": str(e)}), 500
