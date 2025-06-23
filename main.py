from flask import Flask, request, jsonify
from threading import Thread
from twilio.rest import Client
import os
import time
from agenda import criar_evento_agenda
import datetime

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

    # Criar evento teste para amanhã
    amanha = datetime.date.today() + datetime.timedelta(days=1)
    inicio = amanha.strftime("%Y-%m-%d") + "T00:00:00"
    fim = amanha.strftime("%Y-%m-%d") + "T23:59:59"

    criar_evento_agenda(
        "Evento teste Kaizen",
        inicio,
        fim,
        "Evento automático criado para teste"
    )

    while True:
        agora = time.localtime()
        if agora.tm_hour == 6 and agora.tm_min == 0:
            enviar_whatsapp()
            time.sleep(60)
        time.sleep(10)

@app.route("/")
def home():
    return "✅ Kaizen agente em operação contínua."

@app.route("/criar_evento", methods=["POST"])
def criar_evento_endpoint():
    data = request.json
    titulo = data.get("title")
    inicio = data.get("start")
    fim = data.get("end")
    descricao = data.get("description")

    if not all([titulo, inicio, fim]):
        return jsonify({"error": "title, start e end são obrigatórios"}), 400

    try:
        criar_evento_agenda(titulo, inicio, fim, descricao or "")
        return jsonify({"status": "evento criado com sucesso"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    thread = Thread(target=background_task)
    thread.daemon = True
    thread.start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
