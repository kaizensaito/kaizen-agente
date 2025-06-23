import os
from flask import Flask
from threading import Thread
from twilio.rest import Client
import time

app = Flask(__name__)

# Insira diretamente aqui seus dados da Twilio
TWILIO_ACCOUNT_SID = "ACc877afe719c5e6c5b66df5de9c2c26aa"
TWILIO_AUTH_TOKEN = "a7f27cc30999f1af6e856033681ba34f"
WHATSAPP_FROM = "whatsapp:+14155238886"
DESTINOS = ["whatsapp:+5511940217504", "whatsapp:+5511934385115"]

client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

def enviar_whatsapp():
    for numero in DESTINOS:
        message = client.messages.create(
            body="🟢 Kaizen teste imediato: canal WhatsApp ativo.",
            from_=WHATSAPP_FROM,
            to=numero
        )
        print(f"Mensagem enviada para {numero} com SID: {message.sid}")

def background_task():
    enviar_whatsapp()
    while True:
        agora = time.localtime()
        if agora.tm_hour == 6 and agora.tm_min == 0:
            enviar_whatsapp()
            time.sleep(60)
        time.sleep(10)

@app.route("/")
def home():
    return "Kaizen agente ativo (com credenciais hardcoded)"

if __name__ == "__main__":
    thread = Thread(target=background_task)
    thread.daemon = True
    thread.start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

