import requests

APPS_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbxuzGdEmKl6QJ1KPaYMiX8WBxH8yFWst6vEiuc1Ny7QGsrViveYmjRpeFOlMvwfyZVh/exec"

def criar_evento_agenda(titulo, data_inicio, data_fim, descricao):
    payload = {
        "title": titulo,
        "start": data_inicio,
        "end": data_fim,
        "description": descricao
    }
    headers = {'Content-Type': 'application/json'}
    try:
        response = requests.post(APPS_SCRIPT_URL, json=payload, headers=headers)
        print(f"Status code: {response.status_code}")
        print(f"Response text: {response.text}")
        if response.status_code == 200:
            print("Evento criado com sucesso!")
        else:
            print(f"Erro ao criar evento: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Erro na requisição: {e}")
