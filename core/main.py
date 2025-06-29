from modules.auto_learn import AutoLearn
from modules.critic import CriticLLM
from modules.memory import Memory
from modules.notify import Notify
from modules.planner import Planner
from modules.telegram_bot import TelegramBot
from modules.utils import Utils
from flask import Flask

app = Flask(__name__)

# Instancia os módulos
memory = Memory()
auto_learn = AutoLearn(memory)
critic = CriticLLM(memory)
planner = Planner(memory)
notify = Notify()
telegram_bot = TelegramBot(token="7968219889:AAE0QsMpWwkVtHAY9mdsCp35vU3hqkmukOQ", chat_id="2025804227")

# Endpoint simples para healthcheck
@app.route('/health')
def healthcheck():
    return "Kaizen Agent Online", 200

def main():
    # Aqui você pode colocar inicializações adicionais, se precisar
    print("Kaizen agente ativo!")

    # Starta o bot Telegram (assumindo que ele tenha um método .start() que roda em thread)
    telegram_bot.start()

    # Rodar Flask na porta 10000 e todas interfaces
    app.run(host='0.0.0.0', port=10000)

if __name__ == "__main__":
    main()
