# core/main.py
import os
from modules.auto_learn import ciclo_de_aprendizado
from modules.critic import CriticLLM
from modules.planner import definir_objetivos, gerar_acoes, executar_acao, avaliar_resultado
from modules.memory import carregar_memoria, salvar_memoria
from modules.notify import send_whatsapp, send_telegram, send_email
from modules.fetcher import fetch_url_content
from modules.llm import gerar_resposta_com_memoria

def main():
    print("[Kaizen] Iniciando agente...")

    # Carrega memória
    memoria = carregar_memoria()

    # Simula mensagem recebida (exemplo)
    mensagem = "Olá Kaizen, status do sistema?"

    # Gera resposta com memória
    resposta = gerar_resposta_com_memoria("usuario_01", mensagem)
    print(f"Resposta gerada: {resposta}")

    # Atualiza memória com a interação
    memoria.setdefault("historico", []).append({"conteudo": mensagem})
    salvar_memoria(memoria)

    # Executa ciclo de autoaprendizado
    ciclo_de_aprendizado(memoria)

    # Planejamento e execução de ações simples
    objetivos = definir_objetivos()
    for objetivo in objetivos:
        acoes = gerar_acoes(objetivo)
        for acao in acoes:
            executar_acao(acao)
    resultado = avaliar_resultado()
    print(f"Resultado avaliação: {resultado}")

    # Envia heartbeat por notificações
    send_whatsapp("[Kaizen] Heartbeat OK")
    send_telegram(os.getenv("TELEGRAM_CHAT_ID"), "[Kaizen] Heartbeat OK")
    send_email("Kaizen Heartbeat", "Sistema funcionando normalmente.")

if __name__ == "__main__":
    main()
