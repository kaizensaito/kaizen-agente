import requests

TOKEN = "7968219889:AAE0QsMpWwkVtHAY9mdsCp35vU3hqkmukOQ"
CHAT_ID = "2025804227"

def enviar_mensagem_telegram(texto):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": texto}
    try:
        resp = requests.post(url, data=payload)
        if resp.status_code == 200:
            print("Mensagem enviada com sucesso!")
        else:
            print(f"Erro ao enviar mensagem: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"Exceção ao enviar mensagem: {e}")

if __name__ == "__main__":
    enviar_mensagem_telegram("Kaizen ativo e testando envio via Telegram.")
