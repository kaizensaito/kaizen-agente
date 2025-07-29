import os
import io
import sys
import json
import re
import time
import threading
import logging
import requests
import schedule
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, request, jsonify
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

# --- CONFIGURAÇÃO E AMBIENTE ---
load_dotenv()

# Timezone Brasil
TZ = ZoneInfo("America/Sao_Paulo")

# Logging
LOG_FILE = "kaizen.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

# Variáveis de ambiente essenciais
PORT = int(os.getenv("PORT", 10000))

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO")

TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM = os.getenv("TWILIO_FROM_NUMBER")
TWILIO_TO = os.getenv("TWILIO_TO_NUMBER")

RENDER_API_KEY = os.getenv("RENDER_API_KEY")
RENDER_SERVICE_ID = os.getenv("RENDER_SERVICE_ID")

ML_CLIENT_ID = os.getenv("ML_CLIENT_ID")
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET")

# Flask app
app = Flask(__name__)

# Estado do sistema
state = {
    "last_heartbeat": None,
    "last_update_check": None,
    "last_scrape": None,
    "usage_counters": {},
    "log_buffer": [],
}

LOG_MAX_SIZE = 200  # Quantidade máxima de logs mantidos em memória

# Lock para evitar corrida de threads
state_lock = threading.Lock()

# --- FUNÇÕES UTILITÁRIAS ---

def log(msg, level=logging.INFO):
    ts = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    full_msg = f"[{ts}] {msg}"
    with state_lock:
        state["log_buffer"].append(full_msg)
        if len(state["log_buffer"]) > LOG_MAX_SIZE:
            state["log_buffer"].pop(0)
    logging.log(level, msg)

def send_email(subject, body):
    try:
        if not (EMAIL_USER and EMAIL_PASS and EMAIL_TO):
            log("Email credentials missing, skipping email send.", logging.WARNING)
            return
        msg = MIMEMultipart()
        msg['From'] = EMAIL_USER
        msg['To'] = EMAIL_TO
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASS)
        server.send_message(msg)
        server.quit()
        log("[EMAIL] E-mail enviado com sucesso.")
    except Exception as e:
        log(f"[EMAIL] Falha ao enviar email: {e}", logging.ERROR)

def send_whatsapp(message):
    try:
        if not (TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM and TWILIO_TO):
            log("Twilio credentials missing, skipping WhatsApp send.", logging.WARNING)
            return
        from twilio.rest import Client
        client = Client(TWILIO_SID, TWILIO_TOKEN)
        client.messages.create(
            body=message,
            from_=f"whatsapp:{TWILIO_FROM}",
            to=f"whatsapp:{TWILIO_TO}"
        )
        log("[WHATSAPP] Mensagem enviada com sucesso.")
    except Exception as e:
        log(f"[WHATSAPP] Falha ao enviar mensagem: {e}", logging.ERROR)

def deploy_render():
    if not (RENDER_API_KEY and RENDER_SERVICE_ID):
        log("Render API credentials missing, skipping deploy.", logging.WARNING)
        return
    url = f"https://api.render.com/v1/services/{RENDER_SERVICE_ID}/deploys"
    headers = {
        "Authorization": f"Bearer {RENDER_API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        r = requests.post(url, headers=headers, json={})
        if r.status_code == 201:
            log("🚀 Deploy automático no Render iniciado com sucesso!")
        else:
            log(f"❌ Deploy no Render falhou: {r.status_code} {r.text}", logging.ERROR)
    except Exception as e:
        log(f"❌ Exceção ao tentar deploy no Render: {e}", logging.ERROR)

# --- HEARTBEAT ---

def heartbeat():
    while True:
        try:
            now = datetime.now(TZ)
            with state_lock:
                state["last_heartbeat"] = now.isoformat()
            log(f"[HEARTBEAT] Kaizen está vivo e rodando.")
            # Notificações periódicas opcionais
            send_whatsapp(f"Heartbeat Kaizen: {now.strftime('%Y-%m-%d %H:%M:%S')}")
            send_email("Heartbeat Kaizen", f"Kaizen está ativo às {now.strftime('%Y-%m-%d %H:%M:%S')}")
        except Exception as e:
            log(f"[HEARTBEAT] Erro: {e}", logging.ERROR)
        time.sleep(3600)  # A cada 1 hora

# --- WATCHDOG ---

def watchdog():
    while True:
        time.sleep(300)  # Verifica a cada 5 minutos
        with state_lock:
            last = state.get("last_heartbeat")
        if not last:
            log("[WATCHDOG] Nenhum heartbeat detectado, reiniciando serviço...", logging.ERROR)
            os.execv(sys.executable, ['python'] + sys.argv)
        else:
            last_dt = datetime.fromisoformat(last)
            if datetime.now(TZ) - last_dt > timedelta(minutes=10):
                log("[WATCHDOG] Heartbeat atrasado, reiniciando serviço...", logging.ERROR)
                os.execv(sys.executable, ['python'] + sys.argv)

# --- SCRAPING DE PRODUTOS ---

def fetch_url_content(url, max_chars=5000):
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent":"Mozilla/5.0"})
        r.raise_for_status()
        return r.text[:max_chars]
    except Exception as e:
        log(f"[SCRAPE] Erro ao acessar {url}: {e}", logging.ERROR)
        return None

def search_mercadolivre(query, limit=3):
    url = "https://api.mercadolibre.com/sites/MLB/search"
    try:
        r = requests.get(url, params={"q": query, "limit": limit}, timeout=10)
        r.raise_for_status()
        data = r.json()
        results = []
        for item in data.get("results", [])[:limit]:
            results.append({
                "site": "MercadoLivre",
                "title": item.get("title"),
                "price": f"R$ {item.get('price', 0):.2f}",
                "link": item.get("permalink")
            })
        return results
    except Exception as e:
        log(f"[SCRAPE ML] Erro: {e}", logging.ERROR)
        return []

def search_shopee(query, limit=3):
    url = f"https://shopee.com.br/search?keyword={requests.utils.quote(query)}"
    html = fetch_url_content(url, 100000)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    results = []
    cards = soup.select("div._1NoI8_")[:limit]
    for c in cards:
        try:
            title = c.select_one("div._10Wbs-").text.strip()
            link = "https://shopee.com.br" + c.select_one("a")["href"]
            price = c.select_one("span._29R_un").text.strip()
            results.append({"site": "Shopee", "title": title, "price": price, "link": link})
        except Exception:
            continue
    return results

def search_amazon(query, limit=3):
    url = f"https://www.amazon.com.br/s?k={requests.utils.quote(query)}"
    html = fetch_url_content(url, 100000)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    results = []
    cards = soup.select("div.s-result-item[data-component-type='s-search-result']")[:limit]
    for c in cards:
        try:
            title = c.select_one("span.a-size-medium.a-color-base.a-text-normal").text.strip()
            price = c.select_one("span.a-price > span.a-offscreen").text.strip()
            link = "https://www.amazon.com.br" + c.select_one("a.a-link-normal")["href"]
            results.append({"site": "Amazon", "title": title, "price": price, "link": link})
        except Exception:
            continue
    return results

def is_product_query(text):
    return bool(re.search(r"\b(preciso|quero|comprar|valor|preço)\b", text.lower()))

def extract_product_name(text):
    patterns = [
        r"preciso (?:do|da|de)\s+(.+)",
        r"quero comprar\s+(.+)",
        r"quero\s+(.+)",
        r"comprar\s+(.+)",
        r"valor (?:de|do|da)\s+(.+)",
        r"preço (?:de|do|da)\s+(.+)"
    ]
    text_lower = text.lower()
    for pat in patterns:
        m = re.search(pat, text_lower)
        if m:
            return m.group(1).strip()
    return text.strip()

# --- AUTOPDATE ---

def auto_update_check():
    try:
        # Aqui você pode implementar o 'git pull' ou reinício via Render API
        log("[AUTOUPDATE] Checando atualizações remotas...")
        # Exemplo: deploy no Render
        deploy_render()
        with state_lock:
            state["last_update_check"] = datetime.now(TZ).isoformat()
    except Exception as e:
        log(f"[AUTOUPDATE] Erro: {e}", logging.ERROR)

# --- APRENDIZADO SIMPLIFICADO (LOG-BASED) ---

def aprendizado_dinamico():
    try:
        log("[APRENDIZADO] Analisando logs para padrões de falha...")
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()[-1000:]  # Últimas 1000 linhas
        erros = [l for l in lines if re.search(r"erro|fail|exception", l, re.IGNORECASE)]
        if erros:
            log(f"[APRENDIZADO] Detectados {len(erros)} possíveis falhas recentes.")
        else:
            log("[APRENDIZADO] Nenhum erro detectado nas últimas linhas do log.")
    except Exception as e:
        log(f"[APRENDIZADO] Falha na análise de logs: {e}", logging.ERROR)

# --- AGENDAMENTOS ---

def scheduler_loop():
    schedule.every(10).minutes.do(heartbeat)
    schedule.every(30).minutes.do(auto_update_check)
    schedule.every().hour.at(":00").do(learn_safe_wrapper)
    while True:
        schedule.run_pending()
        time.sleep(1)

def learn_safe_wrapper():
    try:
        aprendizado_dinamico()
    except Exception as e:
        log(f"[SCHEDULE] Erro no aprendizado: {e}", logging.ERROR)

# --- FLASK ENDPOINTS ---

@app.route("/", methods=["GET"])
def root():
    with state_lock:
        last_hb = state.get("last_heartbeat")
        last_update = state.get("last_update_check")
        logs = state.get("log_buffer")[-20:]
    return jsonify({
        "status": "online",
        "last_heartbeat": last_hb,
        "last_update_check": last_update,
        "recent_logs": logs,
        "version": "v3.1.0-full-monstro"
    })

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True)
    msg = data.get("message", {}).get("text") or data.get("text") or ""
    log(f"[WEBHOOK] Mensagem recebida: {msg}")
    # Aqui poderia ter processamento e resposta automatizada
    return jsonify({"ok": True, "response": "Mensagem recebida pelo Kaizen."})

@app.route("/search", methods=["GET"])
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "Query inválida ou vazia."}), 400
    if is_product_query(q):
        prod_name = extract_product_name(q)
        ml_res = search_mercadolivre(prod_name)
        sh_res = search_shopee(prod_name)
        am_res = search_amazon(prod_name)
        results = ml_res + sh_res + am_res
        log(f"[SEARCH] Busca realizada para: {prod_name} -> {len(results)} resultados")
        return jsonify(results)
    return jsonify({"msg": "Consulta não parece ser de produto para busca."})

@app.route("/status", methods=["GET"])
def status():
    with state_lock:
        uptime = datetime.now(TZ) - datetime.fromisoformat(state["last_heartbeat"]) if state["last_heartbeat"] else None
    return jsonify({
        "status": "running",
        "heartbeat": state.get("last_heartbeat"),
        "uptime_sec": uptime.total_seconds() if uptime else None,
        "version": "v3.1.0-full-monstro"
    })

# --- STARTUP ---

def start_threads():
    threading.Thread(target=heartbeat, daemon=True).start()
    threading.Thread(target=scheduler_loop, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()

if __name__ == "__main__":
    log("🚀 Kaizen iniciando...")
    start_threads()
    app.run(host="0.0.0.0", port=PORT)
