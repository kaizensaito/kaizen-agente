import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flask import Flask, request, jsonify
from modules.llm import gerar_resposta_com_memoria, gerar_resposta
from utils.fetch import fetch_url_content
from utils.notifications import send_telegram, send_whatsapp

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
        url = msg.split(None, 1)[1]
        content = fetch_url_content(url)
        summary = gerar_resposta(f"Resuma este conteúdo da web:\n\n{content}")
        return jsonify(raw=content, summary=summary)

    return jsonify(reply=gerar_resposta_com_memoria("web", msg))

@app.route('/usage', methods=['GET'])
def usage():
    # Aqui você deve retornar os counters de uso (implemente conforme seu projeto)
    return jsonify({})

@app.route('/test_llm', methods=['GET'])
def test_llm():
    out = {}
    for name, fn in ALL_PROVIDERS.items():
        try:
            out[name] = {"ok": True, "reply": fn("Teste Kaizen")}
        except Exception as e:
            out[name] = {"ok": False, "error": str(e)}
    return jsonify(out)

@app.route('/telegram_webhook', methods=['POST'])
def telegram_webhook():
    payload = request.get_json(force=True).get("message", {})
    txt = payload.get("text", "").strip()
    cid = str(payload.get("chat", {}).get("id", ""))

    if txt.lower().startswith(('/fetch ', '/buscar ')):
        url = txt.split(None, 1)[1]
        content = fetch_url_content(url)
        summary = gerar_resposta(f"Resuma este conteúdo da web:\n\n{content}")
        send_telegram(cid, f"🔎 Conteúdo:\n{content[:500]}\n\n📝 Resumo:\n{summary}")
        return jsonify(ok=True)

    if not txt:
        send_telegram(cid, "⚠️ Mensagem vazia.")
    else:
        resp = gerar_resposta_com_memoria(f"tg:{cid}", txt)
        send_telegram(cid, resp)
    return jsonify(ok=True)
