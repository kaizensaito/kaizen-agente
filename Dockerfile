# Dockerfile
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV LANG=C.UTF-8
ENV PORT=10000

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir -U pip \
    && pip install --no-cache-dir -r requirements.txt

EXPOSE $PORT

CMD ["gunicorn", "--bind", "0.0.0.0:10000", "core.router:app"]
