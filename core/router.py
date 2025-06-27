import sys, os
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from flask import request, jsonify
from modules.llm import gerar_resposta_com_memoria, gerar_resposta

app = None  # Substitua essa linha pelo seu Flask app, se necessário

# Exemplo de rota
from flask import Flask
app = Flask(__name__)

@app.route('/')
def index():
    return "Kaizen online", 200

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json(force=True)
    msg = data.get("message", "").strip()
    if not msg:
        return jsonify(error="mensagem vazia"), 400
    return jsonify(reply=gerar_resposta_com_memoria("web", msg))
