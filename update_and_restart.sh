#!/bin/bash

# Caminho do repo na VM
DIR_REPO=~/kaizen-agente

echo "Atualizando repo em $DIR_REPO..."

cd "$DIR_REPO" || { echo "Erro: diretório não encontrado"; exit 1; }

echo "Fazendo git pull..."
git pull origin main || { echo "Erro no git pull"; exit 1; }

echo "Instalando dependências (se houver)..."
pip3 install -r requirements.txt

echo "Reiniciando Kaizen (processo Python)..."

# Se você roda direto com python3 -m core.main, mate e rode de novo:
PKG="core.main"
PID=$(pgrep -f "$PKG")

if [ -n "$PID" ]; then
  echo "Matando processo $PKG (PID $PID)..."
  kill "$PID"
  sleep 2
else
  echo "Nenhum processo $PKG ativo."
fi

echo "Iniciando Kaizen no background..."
nohup python3 -m core.main > kaizen.log 2>&1 &

echo "Update e restart concluídos."
