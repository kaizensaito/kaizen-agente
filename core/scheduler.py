import threading
import schedule
import time
from modules.notify import heartbeat_job, diario_reflexivo

def iniciar_loops():
    schedule.every().day.at("18:00").do(heartbeat_job)
    schedule.every().day.at("23:00").do(diario_reflexivo)

    def schedule_loop():
        while True:
            schedule.run_pending()
            time.sleep(30)

    threading.Thread(target=schedule_loop, daemon=True).start()
