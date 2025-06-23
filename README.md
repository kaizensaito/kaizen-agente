# Kaizen Agente WhatsApp

Este projeto envia mensagens diárias automáticas via WhatsApp usando Twilio Sandbox.

## Como usar

1. Crie uma conta no [Railway](https://railway.app) e faça login.
2. Crie um repositório no GitHub e suba esses arquivos.
3. No Railway, clique em **New Project > Deploy from GitHub Repo** e conecte seu repositório.
4. Configure as variáveis de ambiente no Railway:

   - `TWILIO_ACCOUNT_SID` = Seu SID da Twilio
   - `TWILIO_AUTH_TOKEN` = Seu Token da Twilio

5. Defina o comando de start como:

```bash
python main.py
