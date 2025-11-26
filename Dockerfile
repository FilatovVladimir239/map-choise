# Dockerfile
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

# Ставим нужные системные пакеты (на всякий случай, для Pillow и прочего)
RUN DEBIAN_FRONTEND=noninteractive apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1. Копируем requirements.txt и ставим зависимости
COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 2. Копируем всё приложение (код, static, splits.html, coordinates.txt и т.д.)
COPY . .

# Можно запускать не от root (опционально, но безопаснее)
RUN groupadd --gid 2000 app && \
    useradd --uid 2000 --gid 2000 -m -d /app app
USER app

# Flask-приложение: файл main.py, объект app
EXPOSE 8080
CMD ["gunicorn", "main:app", "--bind", "0.0.0.0:8080", "--timeout", "60"]
