from flask import Flask
from core.router import router

app = Flask(__name__)
app.register_blueprint(router)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
