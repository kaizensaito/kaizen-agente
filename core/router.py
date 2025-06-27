import sys, os
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from flask import Flask, request, jsonify
from modules.llm import gerar_resposta_com_memoria

app = Flask(__name__)

@app.route('/', methods=['GET'])
def index():
    return "Kaizen online", 200

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json(force=True)
    msg = data.get("message", "").strip()
    if not msg:
        return jsonify(error="mensagem vazia"), 400

    reply = gerar_resposta_com_memoria("web", msg)
    return jsonify(reply=reply)
