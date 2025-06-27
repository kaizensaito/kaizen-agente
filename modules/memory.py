import io
import json
import threading
import logging
from datetime import datetime, timezone
from google.oauth2 import service_account
from googleapiclient.discovery import build as build_drive
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
import os

MEMORY_LOCK = threading.Lock()
SCOPES = ['https://www.googleapis.com/auth/drive']
MEM_FILE = 'kaizen_memory_log.json'

# Carregar credenciais do ambiente
GOOGLE_CREDS = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON", "{}"))

def drive_service():
    creds = service_account.Credentials.from_service_account_info(GOOGLE_CREDS, scopes=SCOPES)
    return build_drive('drive', 'v3', credentials=creds, cache_discovery=False)

def get_file_id(svc):
    res = svc.files().list(
        q=f"name='{MEM_FILE}' and trashed=false",
        spaces='drive',
        fields='files(id,name)',
        pageSize=1
    ).execute()
    files = res.get('files', [])
    return files[0]['id'] if files else None

def read_memory():
    svc = drive_service()
    fid = get_file_id(svc)
    if not fid:
        logging.warning(f"[drive] '{MEM_FILE}' não encontrado, criando novo")
        meta = {"name": MEM_FILE}
        media = MediaIoBaseUpload(io.BytesIO(b"[]"), mimetype="application/json")
        svc.files().create(body=meta, media_body=media, fields="id").execute()
        return []
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, svc.files().get_media(fileId=fid))
    while True:
        done = dl.next_chunk()[1]
        if done:
            break
    buf.seek(0)
    return json.load(buf)

def write_memory(entry):
    with MEMORY_LOCK:
        svc = drive_service()
        fid = get_file_id(svc)
        mem = read_memory()
        mem.append(entry)
        buf = io.BytesIO(json.dumps(mem, indent=2).encode())
        svc.files().update(fileId=fid, media_body=MediaIoBaseUpload(buf, 'application/json')).execute()
