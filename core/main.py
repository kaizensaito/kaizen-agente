# main.py

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
import subprocess
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup
import google.generativeai as genai

load_dotenv()

# Configs
EMAIL = os.getenv("EMAIL")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
WHATSAPP_URL = os.getenv("WHATSAPP_WEBHOOK")
TELEGRAM_URL = os.getenv("TELEGRAM_WEBHOOK")
ML_CLIENT_ID = os.getenv("ML_CLIENT_ID")
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET")
HEARTBEAT_INTERVAL = 1800  # segundos
ZONA = ZoneInfo("America/Sao_Paulo")

memoria_path = "memoria.json"

# Logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# Memória
if os.path.exists(memoria_path):
    with open(memoria_path, "r", encoding="utf-8") as f:
        memoria = json.load(f)
else:
    memoria = {"erros": [], "historico": []}

# LLM
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
llm = genai.GenerativeModel("gemini-1.5-flash-latest")

# App
app = Flask(__name__)

# Utilitários

def salvar_memoria(dados):
    with open(memoria_path, "w", encoding="utf-8") as f:
        json.dump(dados, f, indent=2, ensure_ascii=False)

def registrar_erro(modulo, erro):
    registro = {
        "timestamp": datetime.now(ZONA).isoformat(),
        "modulo": modulo,
        "erro": str(erro)
    }
    memoria["erros"].append(registro)
    salvar_memoria(memoria)
    logging.error(f"[ERRO] {modulo}: {erro}")

def enviar_email(destinatario, assunto, corpo):
    try:
        msg = MIMEMultipart()
        msg["From"] = EMAIL
        msg["To"] = destinatario
        msg["Subject"] = assunto
        msg.attach(MIMEText(corpo, "plain"))
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(EMAIL, EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        logging.info("[EMAIL] Enviado com sucesso.")
    except Exception as e:
        registrar_erro("email", e)

def notificar(mensagem):
    try:
        requests.post(WHATSAPP_URL, json={"mensagem": mensagem})
        requests.post(TELEGRAM_URL, json={"mensagem": mensagem})
    except Exception as e:
        registrar_erro("notificação", e)

def consultar_mercado_livre(produto):
    try:
        url = f"https://api.mercadolibre.com/sites/MLB/search?q={produto}"
        r = requests.get(url)
        return r.json()
    except Exception as e:
        registrar_erro("mercado_livre", e)
        return {}

def auto_update():
    try:
        output = subprocess.check_output(["git", "pull"], stderr=subprocess.STDOUT).decode()
        if "Already up to date" not in output:
            notificar("[AUTO-UPDATE] Código atualizado via git pull. Reiniciando...")
            subprocess.Popen(["pkill", "-f", "main.py"])
        else:
            logging.info("[AUTO-UPDATE] Nenhuma mudança detectada.")
    except Exception as e:
        registrar_erro("auto_update", e)

def auto_aprender():
    try:
        if not memoria["erros"]:
            return "Nenhum erro registrado para análise."
        prompt = "Veja os erros abaixo e sugira melhorias no código do sistema Kaizen:\n"
        for err in memoria["erros"][-5:]:
            prompt += f"- [{err['timestamp']}] ({err['modulo']}): {err['erro']}\n"
        response = llm.generate_content(prompt)
        sugestao = response.text.strip()
        memoria["historico"].append({"entrada": prompt, "sugestao": sugestao})
        salvar_memoria(memoria)

        if "def" in sugestao and "return" in sugestao:
            with open(__file__, "a", encoding="utf-8") as f:
                f.write("\n\n# Sugestão aplicada automaticamente:\n")
                f.write(sugestao)
            logging.info("[SELF-UPDATE] Sugestão aplicada automaticamente ao código.")
            notificar("[AUTO-LEARNING] Nova função aplicada. Reiniciando para efetivar...")
            subprocess.Popen(["pkill", "-f", "main.py"])
            return "Sugestão aplicada. Reinício automático iniciado."

        return sugestao
    except Exception as e:
        registrar_erro("aprendizado", e)
        return "Erro ao tentar aprender com falhas."

def heartbeat():
    try:
        msg = f"Heartbeat {datetime.now(ZONA).strftime('%Y-%m-%d %H:%M:%S')}\nTotal de erros: {len(memoria['erros'])}"
        notificar(msg)
    except Exception as e:
        registrar_erro("heartbeat", e)

def agendar_tarefas():
    schedule.every(HEARTBEAT_INTERVAL).seconds.do(heartbeat)
    schedule.every(4).hours.do(auto_update)
    schedule.every(6).hours.do(auto_aprender)

def executar_agendador():
    while True:
        schedule.run_pending()
        time.sleep(5)

@app.route("/kaizen", methods=["POST"])
def kaizen_route():
    data = request.json
    if not data or "mensagem" not in data:
        return jsonify({"erro": "mensagem ausente"}), 400
    msg = data["mensagem"].strip().lower()
    if "status" in msg:
        return jsonify({"resposta": f"Rodando. Erros: {len(memoria['erros'])}"})
    if msg.startswith("consultar"):
        produto = msg.replace("consultar", "").strip()
        return jsonify(consultar_mercado_livre(produto))
    if "aprender" in msg:
        return jsonify({"resposta": auto_aprender()})
    return jsonify({"resposta": "Comando não reconhecido."})

if __name__ == "__main__":
    agendar_tarefas()
    threading.Thread(target=executar_agendador).start()
    app.run(host="0.0.0.0", port=8080)
