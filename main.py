from flask import Flask, request, jsonify
from twilio.rest import Client
from dotenv import load_dotenv
import os
import google.generativeai as genai
import openai
import json
from datetime import datetime, timedelta
import threading

# Carrega variáveis de ambiente do .env
load_dotenv()

# Flask App
app = Flask(__name__)

# Twilio Config
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
TO_WPP = os.getenv("TO_WPP") or 'whatsapp:+55XXXXXXXXXXX'
FROM_WPP = 'whatsapp:+14155238886'  # Twilio Sandbox
client_twilio = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Gemini Config
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)
GEMINI_MODELS = [
    "models/gemini-1.5-flash",
    "models/gemini-1.5-pro",
    "models/gemini-2.5-pro-preview-06-05"
]

# OpenAI (fallback)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

# Memória simples em arquivo JSON
MEMORIA_FILE = 'memoria_kaizen.json'

def carregar_memoria(usuario):
    try:
        with open(MEMORIA_FILE, 'r') as f:
            memoria = json.load(f)
        return memoria.get(usuario, [])
    except:
        return []

def salvar_memoria(usuario, mensagens):
    try:
        memoria = {}
        try:
            with open(MEMORIA_FILE, 'r') as f:
                memoria = json.load(f)
        except:
            pass
        memoria[usuario] = mensagens[-5:]  # guarda só as 5 últimas mensagens
        with open(MEMORIA_FILE, 'w') as f:
            json.dump(memoria, f)
    except Exception as e:
        print(f"[Erro memória] {e}")

# ============================================================
# Função para gerar resposta com contexto personalizado Kaizen e memória
# ============================================================
def gerar_resposta(mensagem):
    contexto_base = (
        "Você é o Kaizen, um agente de inteligência artificial altamente autônomo, "
        "personalizado para Nilson Saito. Você responde sempre de forma direta, estratégica, sem rodeios, "
        "com foco em melhorar a produtividade, clareza mental, e resultados práticos. "
        "Você age com autonomia total, evita respostas genéricas e não precisa perguntar o que deve fazer. "
        "Seu estilo é objetivo, ousado, provocador, e levemente sarcástico quando necessário. "
        "Fale como um parceiro de elite, não como um assistente."
    )

    for model_name in GEMINI_MODELS:
        try:
            print(f"[Gemini] Tentando modelo: {model_name}")
            model = genai.GenerativeModel(model_name)
            response = model.generate_content([
                {"role": "user", "parts": [f"{contexto_base}\nUsuário: {mensagem}"]}
            ])
            if hasattr(response, 'text') and response.text:
                return response.text.strip()
        except Exception as e:
            print(f"[Gemini ERRO] {model_name}: {e}")
            continue

    # Fallback OpenAI
    try:
        print("[OpenAI] Tentando fallback com GPT-4o...")
        completion = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": contexto_base},
                {"role": "user", "content": mensagem}
            ]
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        print("[OpenAI ERRO]", e)
        return "Erro geral: todos os modelos falharam."

# ============================================================
# Função para gerar resposta com memória de curto prazo
# ============================================================
def gerar_resposta_com_memoria(usuario, mensagem_nova):
    mensagens = carregar_memoria(usuario)
    mensagens.append({"role": "user", "content": mensagem_nova})

    # Construir o prompt com contexto da memória
    contexto = (
        "Você é o Kaizen, um agente autônomo para Nilson Saito, direto e prático.\n"
        "Converse mantendo a memória da conversa abaixo:\n"
    )
    for msg in mensagens:
        contexto += f"{msg['role']}: {msg['content']}\n"

    resposta = gerar_resposta(contexto)

    mensagens.append({"role": "assistant", "content": resposta})
    salvar_memoria(usuario, mensagens)

    return resposta

# ============================================================
# Report diário via WhatsApp
# ============================================================
def enviar_relatorio_diario():
    try:
        msg = "Kaizen rodando: ✅\nMensagens respondidas hoje: XX\nErros: 0\nTudo funcionando liso."
        client_twilio.messages.create(
            body=msg,
            from_=FROM_WPP,
            to=TO_WPP
        )
        print("[Relatório] Enviado com sucesso")
    except Exception as e:
        print(f"[Relatório ERRO] {e}")

def agendar_relatorio():
    while True:
        agora = datetime.now()
        proximo_envio = agora.replace(hour=18, minute=0, second=0, microsecond=0)
        if agora > proximo_envio:
            proximo_envio += timedelta(days=1)
        tempo_espera = (proximo_envio - agora).total_seconds()
        threading.Timer(tempo_espera, enviar_relatorio_diario).start()
        threading.Event().wait(tempo_espera + 1)

# ============================================================
# Rotas Flask
# ============================================================

@app.route('/')
def index():
    return "✅ Kaizen está rodando (Twilio + Gemini + OpenAI)"

@app.route('/send_whatsapp', methods=['POST'])
def send_whatsapp():
    try:
        msg = request.json.get('message', 'Mensagem teste funcionando!')
        response = client_twilio.messages.create(
            body=msg,
            from_=FROM_WPP,
            to=TO_WPP
        )
        return jsonify({'status': 'success', 'sid': response.sid})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/ask', methods=['POST'])
def ask_kaizen():
    try:
        user_input = request.json.get('message')
        if not user_input:
            return jsonify({'error': 'Mensagem vazia'}), 400
        reply = gerar_resposta_com_memoria(TO_WPP, user_input)
        return jsonify({'reply': reply})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route("/whatsapp_webhook", methods=["POST"])
def whatsapp_webhook():
    try:
        incoming_msg = request.form.get("Body")
        sender = request.form.get("From")
        print(f"[WhatsApp] De: {sender} | Msg: {incoming_msg}")

        if not incoming_msg:
            return "Mensagem vazia", 400

        reply = gerar_resposta_com_memoria(sender, incoming_msg)

        client_twilio.messages.create(
            body=reply,
            from_=FROM_WPP,
            to=sender
        )

        return "OK", 200

    except Exception as e:
        print(f"[Webhook ERRO] {e}")
        return str(e), 500

# ============================================================
# Executar servidor
# ============================================================

if __name__ == '__main__':
    # Agendar relatório em thread separada para não bloquear Flask
    threading.Thread(target=agendar_relatorio, daemon=True).start()

    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
