def gerar_resposta(texto):
    # Placeholder básico para teste, troque pela sua IA/LLM real
    return f"Resposta automática para: {texto}"

def gerar_resposta_com_memoria(texto, memoria=None):
    # Função simulada para auto aprendizado (expanda conforme quiser)
    if memoria is None:
        memoria = []
    memoria.append(texto)
    resposta = f"Memória atualizada. Resposta para: {texto}"
    return resposta
