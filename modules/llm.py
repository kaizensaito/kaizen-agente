import openai

openai.api_key = "SUA_OPENAI_API_KEY_AQUI"

def gerar_resposta(mensagem):
    texto = ""
    if "message" in mensagem:
        texto = mensagem["message"].get("text", "")
    if not texto:
        return "Não entendi sua mensagem."
    try:
        resposta = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": texto}]
        )
        return resposta.choices[0].message.content.strip()
    except Exception as e:
        print(f"Erro LLM: {e}")
        return "Erro ao processar a mensagem."

def gerar_resposta_com_memoria(mensagem, memoria):
    # Implementação de memória, se quiser usar
    return gerar_resposta(mensagem)
