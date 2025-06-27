from flask import Flask, request, jsonify
from modules.llm import gerar_resposta_com_memoria, gerar_resposta
from modules.utils import fetch_url_content
from modules.notify import send_telegram

app = Flask(__name__)

@app.route('/', methods=['GET'])
def index():
    return "OK", 200

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json(force=True)
    msg = data.get("message", "").strip()
    if not msg:
        return jsonify(error="mensagem vazia"), 400
    if msg.lower().startswith(('/fetch ', '/buscar ')):
        url = msg.split(None,1)[1]
        content = fetch_url_content(url)
        summary = gerar_resposta(f"Resuma este conteúdo da web:\n\n{content}")
        return jsonify(raw=content, summary=summary)
    return jsonify(reply=gerar_resposta_com_memoria("web", msg))
