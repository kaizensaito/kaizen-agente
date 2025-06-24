from flask import Flask, request, jsonify
from twilio.rest import Client
from openai import OpenAI
from dotenv import load_dotenv
import os
import logging
from concurrent.futures import ThreadPoolExecutor
import atexit

# Carrega variáveis do .env
load_dotenv()

# Configura logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("kaizen_app")

# Flask app
app = Flask(__name__)

# Variáveis Twilio
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER") or "whatsapp:+14155238886"

if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
    logger.error("TWILIO_ACCOUNT_SID ou TWILIO_AUTH_TOKEN ausente.")
    raise Exception("Erro de configuração do Twilio")

client_twilio = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# OpenAI SDK novo (>=1.0.0)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    logger.error("OPENAI_API_KEY não configurada.")
    raise Exception("Erro de configuração do OpenAI")

client_openai = OpenAI(api_key=OPENAI_API_KEY)

# Executor para background tasks
executor = ThreadPoolExecutor(max_workers=4)

# Envio de WhatsApp em segundo plano
def send_whatsapp_background(msg, to_wpp):
    try:
        logger.info(f"[BG] Enviando WhatsApp para {to_wpp}: {msg}")
        response = client_twilio.messages.create(
            body=msg,
            from_=TWILIO_PHONE_NUMBER,
            to=to_wpp
        )
        logger.info(f"[BG] Mensagem enviada. SID: {response.sid}")
        return {'status': 'success', 'sid': response.sid}
    except Exception as e:
        logger.error(f"[BG] Erro WhatsApp: {e}")
        return {'status': 'error', 'error': str(e)}

# Chamada para OpenAI em segundo plano
def ask_openai_background(user_input):
    try:
        logger.info(f"[BG] Prompt recebido: {user_input}")
        response = client_openai.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": user_input}],
            temperature=0.7,
            max_tokens=1000
        )
        reply = response.choices[0].message.content
        logger.info("[BG] Resposta gerada com sucesso.")
        return {'reply': reply}
    except Exception as e:
        logger.error(f"[BG] Erro OpenAI: {e}")
        return {'error': str(e)}

# Rotas Flask
@app.route('/')
def index():
    return "✅ Kaizen rodando com Flask + Twilio + OpenAI (v1) em background"

@app.route('/send_whatsapp', methods=['POST'])
def send_whatsapp():
    data = request.json
    msg = data.get('message')
    to_wpp = data.get('to_wpp')

    if not msg or not to_wpp:
        return jsonify({'status': 'error', 'error': 'message e to_wpp obrigatórios'}), 400

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

    future = executor.submit(ask_openai_background, user_input)
    result = future.result(timeout=20)
    status_code = 200 if 'reply' in result else 500
    return jsonify(result), status_code

# Encerramento do executor com Flask
def shutdown_threadpool():
    logger.info("Encerrando thread pool...")
    executor.shutdown(wait=True)

atexit.register(shutdown_threadpool)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    logger.info(f"Iniciando app na porta {port}")
    app.run(host='0.0.0.0', port=port, threaded=True)
