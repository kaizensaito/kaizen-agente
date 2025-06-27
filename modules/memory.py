import os
import json

CAMINHO_MEMORIA = "data/memoria.json"

def carregar_memoria():
    if not os.path.exists(CAMINHO_MEMORIA):
        return {"conversas": [], "auto_aprendizado": ""}
    
    with open(CAMINHO_MEMORIA, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {"conversas": [], "auto_aprendizado": ""}

def salvar_memoria(memoria):
    with open(CAMINHO_MEMORIA, "w", encoding="utf-8") as f:
        json.dump(memoria, f, indent=2, ensure_ascii=False)
