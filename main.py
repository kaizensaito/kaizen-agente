from flask import Flask, request, jsonify
import os
import openai

app = Flask(__name__)

# Configura sua chave OpenAI pela variável de ambiente
openai.api_key = os.getenv("OPENAI_API_KEY")

if not openai.api_key:
    raise RuntimeError("Chave OpenAI não configurada. Defina a variável de ambiente OPENAI_API_KEY.")

@app.route("/")
def home():
    return "Kaizen agente backend está no ar!"

@app.route("/ask", methods=["POST"])
def ask():
    data = request.json
    if not data or "prompt" not in data:
        return jsonify({"error": "Faltando campo 'prompt' no JSON"}), 400

    prompt = data["prompt"]

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.7,
        )
        answer = response.choices[0].message.content.strip()
        return jsonify({"response": answer})
    except Exception as e:
        return jsonify({"error": f"Erro na API OpenAI: {str(e)}"}), 500

if __name__ == "__main__":
    # Rodar na porta 10000 para compatibilidade com o Render (ou modifique conforme precisar)
    app.run(host="0.0.0.0", port=10000)
