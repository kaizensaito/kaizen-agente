from flask import Flask, request, jsonify
from twilio.rest import Client
from openai import OpenAI
from dotenv import load_dotenv
import os

# Carrega variáveis de ambiente do arquivo .env
load_dotenv()

# Inicializa Flask
app = Flask(__name__)

# Twilio
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
client_twilio = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai = OpenAI(api_key=OPENAI_API_KEY)

# WhatsApp padrão
FROM_WPP = 'whatsapp:+14155238886'  # sandbox Twilio
TO_WPP = os.getenv("TO_WPP") or 'whatsapp:+55XXXXXXXXXXX'  # personalize se quiser


@app.route('/')
def index():
    return "✅ Kaizen está rodando (Flask + Twilio + OpenAI)"


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

        completion = openai.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": user_input}]
        )

        reply = completion.choices[0].message.content
        return jsonify({'reply': reply})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
