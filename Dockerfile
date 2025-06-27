# Dockerfile
FROM python:3.11-slim

# Variáveis de ambiente para portas e utf-8
ENV PYTHONUNBUFFERED=1
ENV LANG=C.UTF-8
ENV PORT=10000

# Cria pasta da app e copia código
WORKDIR /app
COPY . /app

# Instala dependências
RUN pip install --no-cache-dir -U pip \
    && pip install --no-cache-dir -r requirements.txt

# Expõe porta
EXPOSE $PORT

# Comando de inicialização via Gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:10000", "app:app"]