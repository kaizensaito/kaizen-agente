# core/main.py
import os
import sys
import logging
from modules.auto_learn import analisar_interacoes, carregar_aprendizado, salvar_aprendizado
from modules.critic import CriticLLM
from modules.memory import Memory
from modules.notify import Notify
from modules.planner import Planner
from modules.telegram_bot import TelegramBot
from modules.utils import setup_logging

def main():
    setup_logging()
    logger = logging.getLogger("kaizen")
    logger.info("Iniciando Kaizen...")

    # Carrega memória persistente
    memoria = Memory.load()

    # Instancia os módulos principais
    critic = CriticLLM()
    notify = Notify()
    planner = Planner()
    telegram_bot = TelegramBot()

    # Inicia o bot do Telegram (rodar em thread separada dentro do TelegramBot)
    telegram_bot.start()

    # Loop principal (exemplo simples, você ajusta o scheduler)
    try:
        while True:
            # Exemplo de aprendizado automático baseado na memória atual
            insights = analisar_interacoes(memoria.data)
            if insights:
                logger.info(f"Insights novos: {insights}")
                aprendizado_atual = carregar_aprendizado()
                aprendizado_atual.update(insights)
                salvar_aprendizado(aprendizado_atual)

            # Aqui podem vir chamadas ao CriticLLM, Planner, Notify, etc.

            # Pequena pausa para não travar CPU
            import time
            time.sleep(10)

    except KeyboardInterrupt:
        logger.info("Kaizen finalizado pelo usuário.")
        telegram_bot.stop()
        sys.exit(0)

if __name__ == "__main__":
    main()

