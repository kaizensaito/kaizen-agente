from flask import Flask, request, jsonify
from twilio.rest import Client
from dotenv import load_dotenv
import os
import logging
from concurrent.futures import ThreadPoolExecutor
import atexit
import google.generativeai as genai

# Carrega variáveis
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("kaizen_app")

app = Flask(__name__)

# Twilio
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER") or "whatsapp:+14155238886"

if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
    raise Exception("Erro: Twilio não configurado corretamente.")

client_twilio = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Gemini (Google)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise Exception("Erro: Gemini API Key ausente.")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-pro")

executor = ThreadPoolExecutor(max_workers=4)

# WhatsApp
def send_whatsapp_background(msg, to_wpp):
    try:
        logger.info(f"[BG] Enviando WhatsApp: {msg}")
        response = client_twilio.messages.create(
            body=msg,
            from_=TWILIO_PHONE_NUMBER,
            to=to_wpp
        )
        return {'status': 'success', 'sid': response.sid}
    except Exception as e:
        logger.error(f"Erro WhatsApp: {e}")
        return {'status': 'error', 'error': str(e)}

# Gemini Pro
def ask_gemini_background(user_input):
    try:
        logger.info(f"[BG] Pergunta: {user_input}")
        response = model.generate_content(user_input)
        return {'reply': response.text}
    except Exception as e:
        logger.error(f"Erro Gemini: {e}")
        return {'error': str(e)}

@app.route('/')
def index():
    return "✅ Kaizen rodando com Gemini Pro + Twilio"

@app.route('/send_whatsapp', methods=['POST'])
def send_whatsapp():
    data = request.json
    msg = data.get('message')
    to_wpp = data.get('to_wpp')

    if not msg or not to_wpp:
        return jsonify({'status': 'error', 'error': 'message e to_wpp são obrigatórios'}), 400

    future = executor.submit(send_whatsapp_background, msg, to_wpp)
    result = future.result(timeout=15)
    status_code = 200 if result.get('status') == 'success' else 500
    return jsonify(result), status_code

@app.route('/ask', methods=['POST'])
def ask_kaizen():
    data = request.json
    user_input = data.get('message')

    if not user_input:
        return jsonify({'error': 'Mensagem vazia'}), 400

    future = executor.submit(ask_gemini_background, user_input)
    result = future.result(timeout=20)
    status_code = 200 if 'reply' in result else 500
    return jsonify(result), status_code

def shutdown_threadpool():
    logger.info("Encerrando thread pool...")
    executor.shutdown(wait=True)

atexit.register(shutdown_threadpool)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    logger.info(f"Iniciando app na porta {port}")
    app.run(host='0.0.0.0', port=port, threaded=True)
