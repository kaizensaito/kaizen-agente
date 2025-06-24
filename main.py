from flask import Flask, request, jsonify
from twilio.rest import Client
from dotenv import load_dotenv
import os
import google.generativeai as genai
import openai

# Carrega variáveis de ambiente
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

# OpenAI fallback
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

# ============================================
# Função genérica para gerar resposta com fallback
# ============================================
def gerar_resposta(mensagem):
    for model_name in GEMINI_MODELS:
        try:
            print(f"[Gemini] Tentando modelo: {model_name}")
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(mensagem)
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
            messages=[{"role": "user", "content": mensagem}]
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        print("[OpenAI ERRO]", e)
        return "Erro geral: todos os modelos falharam."

# ============================================
# Rotas Flask
# ============================================

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
        reply = gerar_resposta(user_input)
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

        reply = gerar_resposta(incoming_msg)

        client_twilio.messages.create(
            body=reply,
            from_=FROM_WPP,
            to=sender
        )

        return "OK", 200

    except Exception as e:
        print(f"[WhatsApp Webhook ERRO] {e}")
        return str(e), 500

# ============================================
# Iniciar servidor
# ============================================

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
