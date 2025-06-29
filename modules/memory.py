import os
import json
from threading import Lock

CAMINHO_MEMORIA = "data/memoria.json"
MEMORY_LOCK = Lock()

def read_memory():
    if not os.path.exists(CAMINHO_MEMORIA):
        return []

    with MEMORY_LOCK:
        try:
            with open(CAMINHO_MEMORIA, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return []

def write_memory(entry):
    with MEMORY_LOCK:
        memoria = read_memory()
        memoria.append(entry)
        with open(CAMINHO_MEMORIA, "w", encoding="utf-8") as f:
            json.dump(memoria, f, indent=2, ensure_ascii=False)
