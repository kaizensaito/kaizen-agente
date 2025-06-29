import os
import openai

openai.api_key = os.getenv("OPENAI_API_KEY")

def gerar_resposta_com_memoria(user_id, mensagem):
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Você é o Kaizen, assistente direto, prático e inteligente."},
                {"role": "user", "content": mensagem}
            ],
            max_tokens=150,
            temperature=0.7,
        )
        texto = response.choices[0].message.content.strip()
        return texto
    except Exception as e:
        return f"⚠️ Erro na geração da resposta: {e}"
