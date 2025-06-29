# core/main.py

import os
import logging
from modules.auto_learn import ciclo_de_aprendizado
from modules.memory import carregar_memoria, salvar_memoria
from modules.llm import gerar_resposta_com_memoria
from modules.notify import send_whatsapp, send_telegram, send_email
from modules.fetcher import fetch_url_content
from datetime import datetime
import time

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')

def main_loop():
    logging.info("Kaizen iniciado")

    memoria = carregar_memoria()

    while True:
        # Simulação: recebe mensagem do usuário (substituir por input real ou API)
        msg_usuario = input("Você: ").strip()
        if msg_usuario.lower() in ("sair", "exit", "quit"):
            logging.info("Encerrando Kaizen")
            break

        memoria.setdefault("historico", []).append({"conteudo": msg_usuario, "timestamp": datetime.now().isoformat()})

        # Gera resposta com LLM
        resposta = gerar_resposta_com_memoria("usuario", msg_usuario)

        print(f"Kaizen: {resposta}")

        # Atualiza aprendizado automático
        ciclo_de_aprendizado(memoria)

        # Salva memória
        salvar_memoria(memoria)

        # Opcional: notificações - exemplo envio por WhatsApp
        send_whatsapp(f"Nova interação: {msg_usuario}\nResposta: {resposta}")

        time.sleep(1)  # pausa para evitar sobrecarga

if __name__ == "__main__":
    main_loop()
