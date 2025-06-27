from core.router import app
from core.scheduler import iniciar_loops

if __name__ == "__main__":
    iniciar_loops()
    app.run(host="0.0.0.0", port=10000)
