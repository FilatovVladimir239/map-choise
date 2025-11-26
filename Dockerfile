FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

RUN DEBIAN_FRONTEND=noninteractive apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# зависимости
COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# код и файлы
COPY . .

# пользователь (опционально)
RUN groupadd --gid 2000 app && \
    useradd --uid 2000 --gid 2000 -m -d /app app
USER app

# важно: именно этот порт будет слушать Nginx (по доке — 8080 по умолчанию)
EXPOSE 8080

# если файл main.py и объект app
CMD ["gunicorn", "main:app", "--bind", "0.0.0.0:8080", "--timeout", "60"]
