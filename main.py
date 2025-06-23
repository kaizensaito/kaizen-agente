import os
from flask import Flask
from threading import Thread
from twilio.rest import Client
import time
from agenda import criar_evento_agenda

app = Flask(__name__)

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
WHATSAPP_FROM = "whatsapp:+14155238886"
DESTINOS = ["whatsapp:+5511940217504", "whatsapp:+5511934385115"]

client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

def enviar_whatsapp():
    for numero in DESTINOS:
        try:
            message = client.messages.create(
                body="🟢 Kaizen ativo e operacional via WhatsApp.",
                from_=WHATSAPP_FROM,
                to=numero
            )
            print(f"Mensagem enviada para {numero} com SID: {message.sid}")
        except Exception as e:
            print(f"Falha ao enviar mensagem para {numero}: {e}")

def background_task():
    enviar_whatsapp()
    # Exemplo: criar evento agendado toda vez que o bot inicia
    criar_evento_agenda(
        "Plantão Motiva Rodoanel",
        "2025-06-25T06:00:00",
        "2025-06-26T06:00:00",
        "Plantão de 24h no Motiva Rodoanel"
    )
    while True:
        agora = time.localtime()
        if agora.tm_hour == 6 and agora.tm_min == 0:
            enviar_whatsapp()
            # Pode criar evento aqui se quiser automático todo dia
            time.sleep(60)
        time.sleep(10)

@app.route("/")
def home():
    return "✅ Kaizen agente em operação contínua."

if __name__ == "__main__":
    thread = Thread(target=background_task)
    thread.daemon = True
    thread.start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
