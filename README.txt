# 🤖 Kaizen Agent

Assistente autônomo com memória em nuvem, múltiplos LLMs, fallback inteligente, notificações integradas, agendamentos e arquitetura modular para evolução contínua e autocorreção.

---

## 🚀 Visão Geral

- ✅ Modular com Flask, Schedule e Threads
- 🧠 Memória persistente no Google Drive (via API)
- 🧩 LLMs com fallback (OpenAI, Gemini, HF, etc.)
- 🔁 Tarefas autônomas: insights, diário, heartbeat
- 🔔 Notificações: Telegram, WhatsApp, E-mail
- 🧪 Base para futura autocorreção e fine-tune local

---

## 📦 Estrutura de Pastas

# 🤖 Kaizen Agent

Assistente autônomo com memória em nuvem, múltiplos LLMs, fallback inteligente, notificações integradas, agendamentos e arquitetura modular para evolução contínua e autocorreção.

---

## 🚀 Visão Geral

- ✅ Modular com Flask, Schedule e Threads
- 🧠 Memória persistente no Google Drive (via API)
- 🧩 LLMs com fallback (OpenAI, Gemini, HF, etc.)
- 🔁 Tarefas autônomas: insights, diário, heartbeat
- 🔔 Notificações: Telegram, WhatsApp, E-mail
- 🧪 Base para futura autocorreção e fine-tune local

---

## 📦 Estrutura de Pastas


---

## ⚙️ Requisitos

- Python 3.10+
- `.env` configurado com:

```env
OPENAI_API_KEY_MAIN=
GEMINI_API_KEY=
HUGGINGFACE_API_TOKEN=
OPENROUTER_API_KEY=
GOOGLE_CREDENTIALS_JSON='{}'
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_FROM_NUMBER=
TWILIO_TO_NUMBER=
TELEGRAM_TOKEN=
TELEGRAM_CHAT_ID=
GMAIL_USER=
GMAIL_PASS=
PORT=10000

🧠 Rodando localmente
cd kaizen
pip install -r requirements.txt
python core/main.py

📲 Endpoints

| Método | Rota        | Função                          |
| ------ | ----------- | ------------------------------- |
| GET    | `/`         | Health check                    |
| POST   | `/ask`      | Envia pergunta, recebe resposta |
| GET    | `/usage`    | Contadores de uso dos LLMs      |
| GET    | `/test_llm` | Testa todos os LLMs             |


📅 Tarefas Agendadas
18h: Envia heartbeat (status de IAs) via WhatsApp, Telegram, Email

23h: Gera diário reflexivo com base nas interações do dia

🧠 Planejado para Evoluir
 Modulação por arquivos

 Pronto para Vector DB e embeddings

 Logs estruturados

 Reflexão crítica com autocorreção

 Aprendizado via shadow testing

 Fine-tuning offline (Colab/LoRA)

🛠️ To-do Futuro
✅ Suporte a múltiplos canais

🧠 Planejamento com metas e objetivos

🤖 Módulo de auto-correção de código

📈 Testes de regressão automatizados com GitHub Actions

🧬 Integração com banco vetorial para RAG

