#!/bin/bash

DIR="/home/ubuntu/kaizen-agente"
LOGFILE="$DIR/update_log.txt"

echo "=== Atualização iniciada em $(date) ===" >> $LOGFILE

cd $DIR || { echo "Diretório não encontrado!" >> $LOGFILE; exit 1; }

# Atualiza do Git
git pull origin main >> $LOGFILE 2>&1

# Instala dependências (se houver mudanças no requirements)
pip install -r requirements.txt >> $LOGFILE 2>&1

# Mata processo rodando (ajuste conforme seu método, aqui tmux exemplo)
tmux kill-session -t kaizen 2>/dev/null

# Roda em tmux na sessão 'kaizen'
tmux new-session -d -s kaizen "python3 -m core.main >> $DIR/kaizen.log 2>&1"

echo "=== Atualização e restart concluídos em $(date) ===" >> $LOGFILE
