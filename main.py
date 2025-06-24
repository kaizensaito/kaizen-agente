import os
import requests
from dotenv import load_dotenv

# Carrega variáveis do .env
load_dotenv()

API_URL = "https://kaizen-agente.onrender.com/ask"
HEADERS = {"Content-Type": "application/json"}

def enviar_mensagem(mensagem):
    try:
        response = requests.post(API_URL, json={"message": mensagem}, headers=HEADERS)
        response.raise_for_status()
        return response.json().get("response", "[Resposta vazia do Kaizen]")
    except requests.exceptions.HTTPError as errh:
        return f"Erro HTTP: {errh}"
    except requests.exceptions.ConnectionError as errc:
        return f"Erro de conexão: {errc}"
    except requests.exceptions.Timeout as errt:
        return f"Erro de timeout: {errt}"
    except requests.exceptions.RequestException as err:
        return f"Erro na requisição: {err}"

def main():
    print("Kaizen CLI Chat - Digite 'sair' para encerrar\n")
    while True:
        msg = input("Você: ").strip()
        if msg.lower() in ['sair', 'exit']:
            print("Encerrando a sessão com Kaizen.")
            break
        resposta = enviar_mensagem(msg)
        print(f"Kaizen: {resposta}\n")

if __name__ == "__main__":
    main()
