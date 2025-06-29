# ✅ Kaizen Main Reconstruído (modular)
# Comparado e alinhado com a versão funcional original

from flask import Flask, request, jsonify
import threading
import time
import schedule
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
import os
from dotenv import load_dotenv

# 🔧 Carregar variáveis de ambiente
load_dotenv()

# 📦 Imports de módulos internos
from modules.llm import gerar_resposta_com_memoria, ALL_PROVIDERS, usage_counters
from modules.notify import send_telegram, send_whatsapp, send_email
from modules.fetcher import fetch_url_content
from modules.memory import read_memory, write_memory
from modules.planner import definir_objetivos, gerar_acoes, executar_acao, avaliar_resultado
from modules.auto_learn import ciclo_de_aprendizado
from modules.critic import analisar_resposta
from modules.utils import is_product_query, extract_product_name, search_amazon_scrape, search_shopee_scrape, search_mercadolivre_api

# 📍 Configurações gerais
CLIENT_TZ = ZoneInfo("America/Sao_Paulo")
app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ♻️ HEARTBEAT

def heartbeat_job():
    logging.info("[heartbeat] executando")
    report = []
    for name in ALL_PROVIDERS:
        ok = True
        try:
            ALL_PROVIDERS[name]("Teste Kaizen")
        except:
            ok = False
        report.append(f"{name}: {'OK' if ok else 'ERRO'}")
    texto = f"Heartbeat {datetime.now(CLIENT_TZ).strftime('%Y-%m-%d %H:%M:%S')}\n" + "\n".join(report)
    send_whatsapp(texto)
    send_telegram(TELEGRAM_CHAT_ID, texto)
    send_email("Kaizen Heartbeat", texto)

# 🧠 DIÁRIO REFLEXIVO

def diario_reflexivo():
    try:
        mem = read_memory()
        hoje = datetime.now(CLIENT_TZ).date()
        today_entries = [
            m for m in mem
            if datetime.fromisoformat(m["timestamp"]).astimezone(CLIENT_TZ).date() == hoje
        ]
        prompt = (
            "Você é o Kaizen. Com base nestas interações de hoje, gere uma reflexão "
            "sobre padrões de resposta, pontos fortes e onde posso melhorar:\n\n"
            + "\n".join(
                f"- Usuário: {e['entrada']}\n  Kaizen: {e['resposta']}"
                for e in today_entries[-10:]
            )
        )
        reflexao = gerar_resposta_com_memoria("diario", prompt)
        write_memory({
            "timestamp": datetime.now().isoformat(),
            "origem": "diario",
            "entrada": prompt,
            "resposta": reflexao,
            "tipo": "reflexao"
        })
        send_telegram(TELEGRAM_CHAT_ID, f"🧠 Reflexão diária:\n{reflexao}")
    except Exception as e:
        logging.exception("[diario] erro na geração de reflexão")

# 🔁 LOOPS

def loop_autonomo():
    while True:
        try:
            insight = gerar_resposta_com_memoria("loop", "Gere um insight produtivo.")
            send_telegram(TELEGRAM_CHAT_ID, insight)
        except Exception:
            logging.exception("[auto] falha")
        time.sleep(4 * 3600)

def resetar_cotasp():
    while True:
        for k in usage_counters:
            usage_counters[k] = 0
        logging.info("[quota] resetada")
        time.sleep(86400)

def loop_schedule():
    while True:
        schedule.run_pending()
        time.sleep(30)

# 🧠 PLANEJAMENTO DIÁRIO

def planejamento_diario():
    objetivos = definir_objetivos()
    for obj in objetivos:
        acoes = gerar_acoes(obj)
        for acao in acoes:
            executar_acao(acao)
    logging.info("[planner] ações diárias executadas")

# ⏰ AGENDA
schedule.every().day.at("18:00").do(heartbeat_job)
schedule.every().day.at("23:00").do(diario_reflexivo)
schedule.every().day.at("06:00").do(planejamento_diario)

# 🌐 FLASK
@app.route("/", methods=["GET"])
def index():
    return "OK", 200

@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json(force=True)
    msg = data.get("message", "").strip()
    if not msg:
        return jsonify(error="mensagem vazia"), 400

    if msg.lower().startswith(('/fetch ', '/buscar ')):
        url = msg.split(None, 1)[1]
        content = fetch_url_content(url)
        resumo = gerar_resposta_com_memoria("web", f"Resuma:
{content}")
        return jsonify(raw=content, summary=resumo)

    if msg.lower().startswith('/cotacao ') or is_product_query(msg):
        produto = msg.split(None, 1)[1] if msg.lower().startswith('/cotacao ') else extract_product_name(msg)
        cot = []
        cot += search_mercadolivre_api(produto)
        cot += search_shopee_scrape(produto)
        cot += search_amazon_scrape(produto)
        return jsonify(cotacao=cot)

    return jsonify(reply=gerar_resposta_com_memoria("web", msg))

# ▶️ START
threading.Thread(target=loop_autonomo, daemon=True).start()
threading.Thread(target=resetar_cotasp, daemon=True).start()
threading.Thread(target=loop_schedule, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
