import json
import os
from datetime import datetime

LEARNING_FILE = "data/auto_learning.json"
LOG_FILE = "logs/learning_log.txt"

def carregar_aprendizado():
    if os.path.exists(LEARNING_FILE):
        with open(LEARNING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def salvar_aprendizado(dados):
    with open(LEARNING_FILE, "w", encoding="utf-8") as f:
        json.dump(dados, f, indent=2, ensure_ascii=False)

def registrar_log(texto):
    timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{timestamp} {texto}\n")

def analisar_interacoes(memoria: dict):
    insights = {}
    contagem_comandos = {}

    for msg in memoria.get("historico", []):
        conteudo = msg.get("conteudo", "").strip().lower()
        if not conteudo:
            continue

        palavras = conteudo.split()
        for palavra in palavras:
            contagem_comandos[palavra] = contagem_comandos.get(palavra, 0) + 1

    comandos_frequentes = sorted(contagem_comandos.items(), key=lambda x: x[1], reverse=True)[:10]
    insights["comandos_mais_usados"] = [cmd for cmd, _ in comandos_frequentes]

    return insights

def ciclo_de_aprendizado(memoria):
    aprendizado_atual = carregar_aprendizado()
    novos_insights = analisar_interacoes(memoria)

    if novos_insights:
        aprendizado_atual.update(novos_insights)
        salvar_aprendizado(aprendizado_atual)
        registrar_log("🧠 Aprendizado atualizado com: " + ", ".join(novos_insights.keys()))
