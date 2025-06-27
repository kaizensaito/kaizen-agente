FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV LANG=C.UTF-8
ENV PORT=10000

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir -U pip \
    && pip install --no-cache-dir -r requirements.txt

EXPOSE $PORT

CMD ["python3", "-m", "core.main"]
