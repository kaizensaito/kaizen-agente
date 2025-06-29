# 🔥 main.py FINAL - VERSÃO HÍBRIDA (LEGADO + CORREÇÕES)

from flask import Flask, request, jsonify
from modules.llm import gerar_resposta_com_memoria
from modules.fetcher import fetch_url_content
from modules.utils import is_product_query, extract_product_name
from modules.critic import analisar_resposta
from modules.planner import definir_objetivos, gerar_acoes, executar_acao, avaliar_resultado
from modules.memory import carregar_memoria, salvar_memoria
from modules.notify import send_telegram
from datetime import datetime
import traceback

app = Flask(__name__)

@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json(force=True)
    msg = data.get("message", "").strip()
    if not msg:
        return jsonify(error="mensagem vazia"), 400

    try:
        # 🔎 Coleta de conteúdo externo
        if msg.lower().startswith(('/fetch ', '/buscar ')):
            url = msg.split(None, 1)[1]
            content = fetch_url_content(url)
            resumo = gerar_resposta_com_memoria("web", f"Resuma este conteúdo:
\n{content}")
            return jsonify(raw=content, summary=resumo)

        # 📊 Cotação de produtos
        if msg.lower().startswith('/cotacao ') or is_product_query(msg):
            produto = (
                msg.split(None, 1)[1]
                if msg.lower().startswith('/cotacao ')
                else extract_product_name(msg)
            )
            from modules.fetcher import search_mercadolivre_api, search_shopee_scrape, search_amazon_scrape
            cot = []
            cot += search_mercadolivre_api(produto)
            cot += search_shopee_scrape(produto)
            cot += search_amazon_scrape(produto)
            return jsonify(cotacao=cot)

        # 🧠 Gera resposta com memória
        resposta = gerar_resposta_com_memoria("web", msg)

        # 🔍 Autocrítica da resposta
        feedback = analisar_resposta(msg, resposta)

        # 🧠 Autoaprendizado
        memoria = carregar_memoria()
        memoria["conversas"].append({"entrada": msg, "resposta": resposta})
        salvar_memoria(memoria)

        # 🔄 Autoexecução de planejamento
        objetivos = definir_objetivos()
        for obj in objetivos:
            acoes = gerar_acoes(obj)
            for acao in acoes:
                executar_acao(acao)
        avaliacao = avaliar_resultado()

        return jsonify(reply=resposta, feedback=feedback, autoeval=avaliacao)

    except Exception as e:
        traceback.print_exc()
        send_telegram("2025804227", f"❌ Erro no /ask: {e}")
        return jsonify(error=str(e)), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
