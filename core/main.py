import os
import io
import json
import time
import threading
import logging
import requests
import schedule
import smtplib
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup

from modules.memory import read_memory, write_memory
from modules.llm import ALL_PROVIDERS, gerar_resposta, gerar_resposta_com_memoria
from modules.notify import send_telegram, send_whatsapp, send_email
from modules.fetcher import fetch_url_content
from modules.auto_learn import analisar_interacoes, carregar_aprendizado, salvar_aprendizado, registrar_log
from modules.critic import analisar_resposta, gerar_correcoes
from modules.planner import definir_objetivos, gerar_acoes, executar_acao, avaliar_resultado

# Configurações básicas
load_dotenv()
app = Flask(__name__)
CLIENT_TZ = ZoneInfo("America/Sao_Paulo")
MEMORY_LOCK = threading.Lock()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Variáveis sensíveis e credenciais
OPENAI_KEY         = os.getenv("OPENAI_API_KEY_MAIN")
GEMINI_KEY         = os.getenv("GEMINI_API_KEY")
HF_TOKEN           = os.getenv("HUGGINGFACE_API_TOKEN")
OR_KEY             = os.getenv("OPENROUTER_API_KEY")
GOOGLE_CREDS       = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON", "{}"))
TELEGRAM_TOKEN     = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN  = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM        = os.getenv("TWILIO_FROM_NUMBER")
TWILIO_TO          = os.getenv("TWILIO_TO_NUMBER")
GMAIL_USER         = os.getenv("GMAIL_USER")
GMAIL_PASS         = os.getenv("GMAIL_PASS")

# Contadores de uso das IAs e cache simples
usage_counters = {p: 0 for p in ALL_PROVIDERS.keys()}
DAILY_LIMITS = {"gemini": 50}
CACHE = {}
_fallback_lock = threading.Lock()

def within_limit(provider):
    return usage_counters[provider] < DAILY_LIMITS.get(provider, float("inf"))

def cached(provider, fn, txt):
    return CACHE.setdefault((provider, txt), fn(txt))

# Monta contexto para conversas, usando histórico da memória
def build_context(channel, msg):
    mem = read_memory()
    hist = [m for m in mem.get("conversas", []) if m.get("origem") == channel]
    parts, size = [], 0
    for h in reversed(hist):
        snippet = f"Usuário: {h['entrada']}\nKaizen: {h['resposta']}\n"
        if size + len(snippet) > 4000 * 0.8:
            break
        parts.insert(0, snippet)
        size += len(snippet)
    parts.append(f"Usuário: {msg}")
    SYSTEM_PROMPT = (
        "Você é o Kaizen: assistente autônomo, direto e levemente sarcástico, "
        "que provoca Nilson Saito e impulsiona a melhoria contínua."
    )
    ctx = SYSTEM_PROMPT + "\n" + "".join(parts)
    return ctx[-4000:] if len(ctx) > 4000 else ctx

# Gerar resposta com fallback entre múltiplos providers, cache e limite diário
def gerar_resposta(text):
    with _fallback_lock:
        seq = list(ALL_PROVIDERS.keys())
    for prov in seq:
        if not within_limit(prov):
            continue
        try:
            logging.info(f"[fallback] tentando {prov}")
            out = cached(prov, ALL_PROVIDERS[prov], text)
            if not out.strip():
                raise RuntimeError(f"{prov} retornou vazio")
            usage_counters[prov] += 1
            with _fallback_lock:
                seq.remove(prov)
                seq.insert(0, prov)
            return out
        except Exception as e:
            logging.warning(f"{prov} falhou: {e}")
    return "⚠️ Todas as IAs falharam."

# Gera resposta e grava na memória local (arquivo JSON)
def gerar_resposta_com_memoria(channel, msg):
    resp = gerar_resposta(build_context(channel, msg))
    if resp.startswith("⚠️"):
        return resp
    mem = read_memory()
    if "conversas" not in mem:
        mem["conversas"] = []
    mem["conversas"].append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "origem": channel,
        "entrada": msg,
        "resposta": resp
    })
    write_memory(mem)
    return resp

# Loop autônomo que gera insights e salva logs
def autonomous_loop():
    while True:
        try:
            insight = gerar_resposta_com_memoria("saito", "Gere um insight produtivo.")
            registrar_log(insight)
            send_telegram(TELEGRAM_CHAT_ID, insight)
        except Exception:
            logging.exception("[auto] falhou no loop")
        time.sleep(4 * 3600)

# Reset diário dos limites de uso
def reset_daily_counters():
    while True:
        now = datetime.now(timezone.utc)
        next_reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        time.sleep((next_reset - now).total_seconds())
        for k in usage_counters:
            usage_counters[k] = 0
        logging.info("[quota] resetada")

# Job diário de heartbeat para garantir funcionamento e alertar
def heartbeat_job():
    logging.info("[heartbeat] executando")
    report = []
    for name, fn in ALL_PROVIDERS.items():
        try:
            fn("Teste Kaizen")
            report.append(f"{name}: OK")
        except Exception:
            report.append(f"{name}: ERRO")
    texto = f"Heartbeat {datetime.now(CLIENT_TZ).strftime('%Y-%m-%d %H:%M:%S')}\n" + "\n".join(report)
    send_whatsapp(texto)
    send_telegram(TELEGRAM_CHAT_ID, texto)
    send_email("Kaizen Heartbeat", texto)

# Reflexão diária baseada nas interações salvas na memória
def diario_reflexivo():
    try:
        mem = read_memory()
        hoje = datetime.now(CLIENT_TZ).date()
        hoje_entradas = [
            m for m in mem.get("conversas", [])
            if datetime.fromisoformat(m["timestamp"]).astimezone(CLIENT_TZ).date() == hoje
        ]
        prompt = (
            "Você é o Kaizen. Com base nestas interações de hoje, gere uma reflexão "
            "sobre padrões de resposta, pontos fortes e onde posso melhorar:\n\n"
            + "\n".join(
                f"- Usuário: {e['entrada']}\n  Kaizen: {e['resposta']}"
                for e in hoje_entradas[-10:]
            )
        )
        reflexao = gerar_resposta(prompt)
        mem.setdefault("conversas", []).append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "origem": "diario",
            "entrada": prompt,
            "resposta": reflexao,
            "tipo": "reflexao"
        })
        write_memory(mem)
        send_telegram(TELEGRAM_CHAT_ID, f"🧠 Reflexão diária:\n{reflexao}")
        logging.info("[diario] reflexão criada e enviada")
    except Exception:
        logging.exception("[diario] falhou ao gerar reflexão")

# Função para buscar conteúdo e retornar resumo
@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json(force=True)
    msg = data.get("message", "").strip()
    if not msg:
        return jsonify(error="mensagem vazia"), 400

    # Comandos /fetch ou /buscar
    if msg.lower().startswith(('/fetch ', '/buscar ')):
        url = msg.split(None, 1)[1]
        content = fetch_url_content(url)
        summary = gerar_resposta(f"Resuma este conteúdo da web:\n\n{content}")
        return jsonify(raw=content, summary=summary)

    # Cotação explícita ou implícita
    if msg.lower().startswith('/cotacao ') or re.search(r"\b(preciso|quero|comprar|valor|preço)\b", msg.lower()):
        produto = (
            msg.split(None, 1)[1]
            if msg.lower().startswith('/cotacao ')
            else re.sub(r".*?(?:do|da|de)\s+", "", msg.lower())
        )
        # Aqui você pode importar suas funções específicas de busca (ml, shopee, amazon)
        cotacoes = []
        # Exemplo fictício:
        # cotacoes += search_mercadolivre_api(produto)
        # cotacoes += search_shopee_scrape(produto)
        # cotacoes += search_amazon_scrape(produto)
        return jsonify(cotacao=cotacoes)

    # Resposta padrão com memória
    resposta = gerar_resposta_com_memoria("web", msg)
    return jsonify(reply=resposta)

@app.route('/', methods=['GET'])
def index():
    return "OK", 200

@app.route('/usage', methods=['GET'])
def usage():
    return jsonify(usage_counters)

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

    if not txt:
        send_telegram(cid, "⚠️ Mensagem vazia.")
        return jsonify(ok=True)

    if txt.lower().startswith(('/fetch ', '/buscar ')):
        url = txt.split(None, 1)[1]
        content = fetch_url_content(url)
        summary = gerar_resposta(f"Resuma este conteúdo da web:\n\n{content}")
        send_telegram(cid, f"🔎 Conteúdo:\n{content[:500]}\n\n📝 Resumo:\n{summary}")
        return jsonify(ok=True)

    if txt.lower().startswith('/cotacao ') or re.search(r"\b(preciso|quero|comprar|valor|preço)\b", txt.lower()):
        produto = (
            txt.split(None, 1)[1]
            if txt.lower().startswith('/cotacao ')
            else re.sub(r".*?(?:do|da|de)\s+", "", txt.lower())
        )
        cotacoes = []
        # Exemplo fictício:
        # cotacoes += search_mercadolivre_api(produto)
        # cotacoes += search_shopee_scrape(produto)
        # cotacoes += search_amazon_scrape(produto)
        msg = f"📊 Cotação para *{produto}*:\n\n"
        for c in cotacoes:
            msg += f"[{c['site']}] {c['title']}\n{c['price']}\n{c['link']}\n\n"
        send_telegram(cid, msg)
        return jsonify(ok=True)

    resp = gerar_resposta_com_memoria(f"tg:{cid}", txt)
    send_telegram(cid, resp)
    return jsonify(ok=True)

# Agendamentos com schedule
schedule.every().day.at("18:00").do(heartbeat_job)
schedule.every().day.at("23:00").do(diario_reflexivo)

def schedule_loop():
    while True:
        schedule.run_pending()
        time.sleep(30)

# Inicialização dos loops autônomos em threads daemon
threading.Thread(target=autonomous_loop, daemon=True).start()
threading.Thread(target=reset_daily_counters, daemon=True).start()
threading.Thread(target=schedule_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
