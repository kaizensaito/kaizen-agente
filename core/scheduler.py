import time
import schedule
import threading
from datetime import datetime
from utils.notifications import send_telegram, send_whatsapp
from modules.llm import gerar_resposta_com_memoria
from modules.memory import carregar_memoria, salvar_memoria

def heartbeat():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = f"💓 Kaizen vivo em {now}"
    print(msg)
    send_telegram(msg)
    send_whatsapp(msg)

def auto_aprendizado():
    memoria = carregar_memoria()
    conversa = memoria.get("conversas", [])[-1] if memoria.get("conversas") else None
    if conversa:
        resposta = gerar_resposta_com_memoria(conversa["mensagem"])
        memoria["auto_aprendizado"] = resposta
        salvar_memoria(memoria)
        print("🧠 Autoaprendizado atualizado.")
    else:
        print("🧠 Nenhuma conversa recente para aprender.")

def iniciar_agendamentos():
    print("📅 Iniciando agendamentos...")
    schedule.every(1).hours.do(heartbeat)
    schedule.every(2).hours.do(auto_aprendizado)

    def run():
        while True:
            schedule.run_pending()
            time.sleep(5)

    t = threading.Thread(target=run)
    t.daemon = True
    t.start()
