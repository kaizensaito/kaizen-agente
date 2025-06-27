import os
import logging
import requests
from openai import OpenAI
import google.generativeai as genai
from dotenv import load_dotenv
from modules.memory import read_memory, write_memory

load_dotenv()
OPENAI_KEY = os.getenv("OPENAI_API_KEY_MAIN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
HF_TOKEN = os.getenv("HUGGINGFACE_API_TOKEN")
OR_KEY = os.getenv("OPENROUTER_API_KEY")

openai_client = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)

SYSTEM_PROMPT = "Você é o Kaizen: assistente autônomo, direto e levemente sarcástico."

ALL_PROVIDERS = {}

def call_openai(model, text):
    return openai_client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": text}],
        temperature=0.7
    ).choices[0].message.content.strip()

if OPENAI_KEY:
    ALL_PROVIDERS["gpt-3.5-turbo"] = lambda t: call_openai("gpt-3.5-turbo", t)

FALLBACK_ORDER = list(ALL_PROVIDERS)
usage_counters = {p: 0 for p in FALLBACK_ORDER}
MAX_CTX = 4000
CACHE = {}

def gerar_resposta(text):
    for p in FALLBACK_ORDER:
        try:
            if (p, text) not in CACHE:
                CACHE[(p, text)] = ALL_PROVIDERS[p](text)
            usage_counters[p] += 1
            return CACHE[(p, text)]
        except Exception as e:
            logging.warning(f"{p} falhou: {e}")
    return "⚠️ Todas as IAs falharam."

def build_context(channel, msg):
    mem = read_memory()
    hist = [m for m in mem if m["origem"] == channel]
    parts, size = [], 0
    for h in reversed(hist):
        b = f"Usuário: {h['entrada']}\nKaizen: {h['resposta']}\n"
        if size + len(b) > MAX_CTX * 0.8: break
        parts.insert(0, b)
        size += len(b)
    parts.append(f"Usuário: {msg}")
    ctx = SYSTEM_PROMPT + "\n" + "".join(parts)
    return ctx[-MAX_CTX:] if len(ctx) > MAX_CTX else ctx

def gerar_resposta_com_memoria(channel, msg):
    resp = gerar_resposta(build_context(channel, msg))
    if resp.startswith("⚠️"):
        return resp
    write_memory({
        "timestamp": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
        "origem": channel,
        "entrada": msg,
        "resposta": resp
    })
    return resp
