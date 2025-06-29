# core/main.py

import os
import json
import logging
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from modules.llm import gerar_resposta_com_memoria
from modules.notify import send_telegram, send_whatsapp, send_email
from modules.fetcher import fetch_url_content
from modules.memory import read_memory
from modules.scheduler import iniciar_agendamentos
from modules.planner import definir_objetivos, gerar_acoes, executar_acao
from modules.auto_learn import ciclo_de_aprendizado
from modules.critic import analisar_resposta
from zoneinfo import ZoneInfo
from datetime import datetime

load_dotenv()

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CLIENT_TZ = ZoneInfo("America/Sao_Paulo")

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

@app.route('/', methods=['GET'])
def index():
    return "Kaizen rodando.", 200

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json(force=True)
    msg = data.get("message", "").strip()
    if not msg:
        return jsonify(error="mensagem vazia"), 400

    if msg.lower().startswith(('/fetch ', '/buscar ')):
        url = msg.split(None, 1)[1]
        content = fetch_url_content(url)
        resumo = gerar_resposta_com_memoria("web", f"Resuma este conteúdo:\n\n{content}")
        return jsonify(raw=content[:1000], summary=resumo)

    resposta = gerar_resposta_com_memoria("web", msg)
    return jsonify(reply=resposta)

@app.route('/refletir', methods=['POST'])
def refletir():
    memoria = read_memory()
    ciclo_de_aprendizado(memoria)
    return jsonify(status="ok", mensagem="Reflexão e aprendizado executados.")

@app.route('/objetivos', methods=['GET'])
def objetivos():
    lista = definir_objetivos()
    return jsonify(objetivos=lista)

@app.route('/executar', methods=['POST'])
def executar():
    data = request.get_json(force=True)
    objetivo = data.get("objetivo", "")
    acoes = gerar_acoes(objetivo)
    for acao in acoes:
        executar_acao(acao)
    return jsonify(status="ok", acoes=acoes)

@app.route('/test', methods=['GET'])
def test():
    agora = datetime.now(CLIENT_TZ).strftime("%Y-%m-%d %H:%M:%S")
    send_telegram(TELEGRAM_CHAT_ID, f"Heartbeat de teste: {agora}")
    send_whatsapp(f"[Kaizen Test] {agora}")
    send_email("Kaizen Test", f"Teste de notificacao em {agora}")
    return jsonify(ok=True, mensagem="Notificações enviadas")

if __name__ == "__main__":
    iniciar_agendamentos()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
