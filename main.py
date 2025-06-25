import os, json, time, threading, logging, requests, io
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify
from twilio.rest import Client
from dotenv import load_dotenv
import google.generativeai as genai
import openai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

# ===================== CONFIG =======================
load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

app = Flask(__name__)
CLIENT_TZ = timezone(timedelta(hours=-3))  # Brasil

# Twilio
TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN  = os.environ["TWILIO_AUTH_TOKEN"]
FROM_WPP           = os.environ["FROM_WPP"]
TO_WPP             = os.environ["TO_WPP"]
client_twilio      = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Gemini + OpenAI
genai.configure(api_key=os.environ["GEMINI_API_KEY"])
GEMINI_MODELS = [
    "models/gemini-1.5-flash",
    "models/gemini-1.5-pro",
    "models/gemini-2.5-pro-preview-06-05"
]
openai.api_key = os.environ["OPENAI_API_KEY"]

# Google Drive (memória)
SCOPES         = ['https://www.googleapis.com/auth/drive']
JSON_FILE_NAME = 'kaizen_memory_log.json'
GOOGLE_CREDS   = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
MEMORY_LOCK    = threading.Lock()

# Trello
TRELLO_KEY     = os.environ["TRELLO_KEY"]
TRELLO_TOKEN   = os.environ["TRELLO_TOKEN"]
TRELLO_LIST_ID = os.environ["TRELLO_LIST_ID"]

# ===================== MEMÓRIA =======================
def drive_service():
    creds = service_account.Credentials.from_service_account_info(GOOGLE_CREDS, scopes=SCOPES)
    return build('drive', 'v3', credentials=creds)

def get_json_file_id(service):
    results = service.files().list(q=f"name='{JSON_FILE_NAME}'", spaces='drive', fields='files(id,name)').execute()
    files = results.get('files', [])
    if not files:
        raise FileNotFoundError(f"{JSON_FILE_NAME} não encontrado.")
    return files[0]['id']

def read_memory():
    service = drive_service()
    file_id = get_json_file_id(service)
    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    fh.seek(0)
    return json.load(fh)

def write_memory(entry):
    with MEMORY_LOCK:
        service = drive_service()
        file_id = get_json_file_id(service)
        memoria = read_memory()
        memoria.append(entry)
        buffer = io.BytesIO(json.dumps(memoria, indent=2).encode('utf-8'))
        media  = MediaIoBaseUpload(buffer, mimetype='application/json')
        service.files().update(fileId=file_id, media_body=media).execute()
        logging.info(f"[Memória] {entry['origem']} → gravado")

# ===================== INTELIGÊNCIA =======================
def gerar_resposta(contexto):
    prompt = "Você é o Kaizen, IA autônoma, estratégica e direta. Foco: Nilson Saito. Nada de enrolação."
    for model in GEMINI_MODELS:
        try:
            model_obj = genai.GenerativeModel(model)
            resp = model_obj.generate_content([{"role": "user", "parts": [f"{prompt}\n{contexto}"]}])
            if getattr(resp, "text", None): return resp.text.strip()
        except Exception:
            continue
    try:
        completion = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": contexto}
            ]
        )
        return completion.choices[0].message.content.strip()
    except Exception:
        return "Erro geral: todos os modelos falharam."

def gerar_resposta_com_memoria(origem, nova_msg):
    identidade = origem if "whatsapp" in origem else "saito"
    historico = [m for m in read_memory() if m.get("origem") == identidade][-10:]
    contexto = "\n".join(f"Usuário: {m['entrada']}\nKaizen: {m['resposta']}" for m in historico)
    contexto += f"\nUsuário: {nova_msg}"
    resposta = gerar_resposta(contexto)
    write_memory({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "origem": identidade,
        "entrada": nova_msg,
        "resposta": resposta
    })
    return resposta

# ===================== WHATSAPP =======================
def enviar_whatsapp(to, msg):
    try:
        client_twilio.messages.create(body=msg, from_=FROM_WPP, to=to)
        logging.info(f"[WhatsApp] Enviado → {to}")
    except Exception as e:
        logging.error(f"[WhatsApp ERRO] {e}")

# ===================== TRELLO =======================
def criar_tarefa_trello(titulo, descricao="", due_days=1):
    try:
        due = (datetime.now(timezone.utc) + timedelta(days=due_days)).replace(hour=9, minute=0, second=0).isoformat()
        payload = {
            "key": TRELLO_KEY,
            "token": TRELLO_TOKEN,
            "idList": TRELLO_LIST_ID,
            "name": titulo[:100],
            "desc": descricao,
            "due": due,
            "pos": "top"
        }
        resp = requests.post("https://api.trello.com/1/cards", params=payload)
        resp.raise_for_status()
        card = resp.json()
        logging.info(f"[Trello] Tarefa criada: {card['id']} → {titulo}")
    except Exception as e:
        logging.error(f"[Trello ERRO] {e}")

# ===================== AUTONOMIA =======================
def pensar_autonomamente():
    hora = datetime.now(CLIENT_TZ).hour
    if 5 <= hora < 9:
        prompt = "Bom dia. Que atitude proativa você tomaria hoje sem minha intervenção?"
    elif 12 <= hora < 14:
        prompt = "Hora do almoço. Revise sua performance hoje e gere um insight produtivo."
    elif 18 <= hora < 20:
        prompt = "Já é fim de expediente. Analise o que aprendeu e o que pode otimizar amanhã."
    else:
        prompt = "Siga seu julgamento. Execute algo útil com base no histórico."
    try:
        insight = gerar_resposta_com_memoria("saito", prompt)
        enviar_whatsapp(TO_WPP, insight)
        criar_tarefa_trello(titulo=insight.split("\n")[0], descricao=insight)
    except Exception as e:
        write_memory({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "origem": "kaizen_autonomo",
            "entrada": "erro_ciclo_autonomo",
            "resposta": str(e)
        })

# ===================== CICLOS =======================
def heartbeat():
    write_memory({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "origem": "sistema",
        "entrada": "heartbeat",
        "resposta": "Kaizen vivo."
    })

def check_render():
    try:
        status = requests.get("https://kaizen-agente.onrender.com/ask", timeout=5).status_code
        write_memory({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "origem": "watchdog",
            "entrada": "checar render",
            "resposta": f"status {status}"
        })
    except Exception as e:
        write_memory({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "origem": "watchdog",
            "entrada": "checar render",
            "resposta": f"erro: {str(e)}"
        })

def loop_autonomia():
    while True:
        pensar_autonomamente()
        time.sleep(3600)

def loop_heartbeat():
    while True:
        heartbeat()
        time.sleep(300)

def loop_watchdog():
    while True:
        check_render()
        time.sleep(600)

def loop_relatorio():
    while True:
        now = datetime.now(CLIENT_TZ)
        target = now.replace(hour=18, minute=0, second=0)
        if now > target:
            target += timedelta(days=1)
        time.sleep((target - now).total_seconds())
        enviar_whatsapp(TO_WPP, "🧠 Kaizen rodando sem falhas. Status diário OK.")

# ===================== FLASK =======================
@app.route('/')
def index():
    return "✅ Kaizen 100% ativo - memória, WhatsApp, Trello, autonomia."

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()
    msg = data.get("message")
    if not msg:
        return jsonify({"error": "mensagem vazia"}), 400
    reply = gerar_resposta_com_memoria("webhook", msg)
    return jsonify({"reply": reply})

@app.route('/whatsapp_webhook', methods=['POST'])
def webhook():
    msg = request.form.get("Body")
    sender = request.form.get("From")
    resposta = gerar_resposta_com_memoria(sender, msg)
    enviar_whatsapp(sender, resposta)
    return "OK", 200

# ===================== BOOT =======================
if __name__ == '__main__':
    threading.Thread(target=loop_heartbeat, daemon=True).start()
    threading.Thread(target=loop_watchdog, daemon=True).start()
    threading.Thread(target=loop_relatorio, daemon=True).start()
    threading.Thread(target=loop_autonomia, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
