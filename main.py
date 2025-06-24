from flask import Flask, request, jsonify
from twilio.rest import Client
import os

app = Flask(__name__)

# Credenciais Twilio - garanta que estão certinhas nas variáveis de ambiente
account_sid = os.getenv('TWILIO_ACCOUNT_SID')
auth_token = os.getenv('TWILIO_AUTH_TOKEN')
client = Client(account_sid, auth_token)

# Números
from_whatsapp_number = 'whatsapp:+14155238886'  # Número sandbox Twilio padrão
to_whatsapp_number = 'whatsapp:+55XXXXXXXXXXX'  # Seu número com código do Brasil

@app.route('/send_whatsapp', methods=['POST'])
def send_whatsapp():
    try:
        message = client.messages.create(
            body="Mensagem teste funcionando!",
            from_=from_whatsapp_number,
            to=to_whatsapp_number
        )
        return jsonify({'status': 'success', 'sid': message.sid})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/')
def index():
    return "Kaizen rodando - versão WhatsApp OK"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
