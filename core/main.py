import os
import logging
from flask import Flask
from core.router import app as flask_app
from core.scheduler import iniciar_agendamentos

# Configurações básicas de log
if not os.path.exists("logs"):
    os.makedirs("logs")

logging.basicConfig(
    filename="logs/kaizen.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# Envolver o app Flask
app = Flask(__name__)
app.register_blueprint(flask_app)

# Roda tarefas agendadas
iniciar_agendamentos()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
