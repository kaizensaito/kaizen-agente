import os
import sys
import requests

def check_directories():
    print("🧱 Diretórios esperados:")
    for d in ["core", "modules"]:
        exists = os.path.isdir(d)
        print(f"  → {d} : {'OK' if exists else 'FALTA!'}")
    print()

def check_files():
    core_files = ["main.py", "router.py", "scheduler.py", "debug.py"]
    modules_files = ["llm.py", "memory.py", "notifications.py"]
    print("📂 Arquivos no core/:")
    for f in core_files:
        path = os.path.join("core", f)
        print(f"  → {f} : {'OK' if os.path.isfile(path) else 'FALTA!'}")
    print("📂 Arquivos no modules/:")
    for f in modules_files:
        path = os.path.join("modules", f)
        print(f"  → {f} : {'OK' if os.path.isfile(path) else 'FALTA!'}")
    print()

def check_env_vars():
    vars_needed = [
        "TELEGRAM_TOKEN",
        "EMAIL_ORIGEM",
        "EMAIL_SENHA",
        "OPENAI_API_KEY",
        "GOOGLE_CREDENTIALS_JSON",
        "TRELLO_KEY",
        "TWILIO_ACCOUNT_SID"
    ]
    print("🔑 Variáveis de ambiente:")
    missing = False
    for var in vars_needed:
        val = os.getenv(var)
        status = "OK" if val else "FALTA!"
        if not val:
            missing = True
        print(f"  → {var}: {status}")
    print()
    return not missing

def check_telegram_webhook():
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        print("⚠️ TELEGRAM_TOKEN não setado no ambiente.")
        return False
    url = f"https://api.telegram.org/bot{token}/getWebhookInfo"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data.get("ok"):
                webhook_url = data["result"].get("url")
                if webhook_url:
                    print(f"🤖 Webhook Telegram ativo em: {webhook_url}")
                    return True
                else:
                    print("⚠️ Webhook Telegram NÃO configurado.")
                    return False
            else:
                print("⚠️ Erro na resposta do Telegram API:", data)
                return False
        else:
            print(f"⚠️ Erro HTTP ao consultar webhook: {r.status_code}")
            return False
    except Exception as e:
        print("⚠️ Exception ao consultar webhook Telegram:", str(e))
        return False

def main():
    print("=== CHECKLIST KAIZEN - STATUS DO AMBIENTE ===\n")
    check_directories()
    check_files()
    env_ok = check_env_vars()
    if not env_ok:
        print("❌ Variáveis de ambiente críticas faltando. Corrija antes de prosseguir.\n")
    else:
        print("✅ Variáveis de ambiente básicas OK.\n")
    webhook_ok = check_telegram_webhook()
    if not webhook_ok:
        print("❌ Problema com webhook Telegram.\n")
    else:
        print("✅ Webhook Telegram OK.\n")

    print("=== FIM DO CHECK ===")

if __name__ == "__main__":
    main()
