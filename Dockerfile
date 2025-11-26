FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

RUN DEBIAN_FRONTEND=noninteractive apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Ставим нужные Python-библиотеки напрямую
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir Flask Pillow beautifulsoup4

# Копируем код и файлы
COPY . .

# Пользователь (можно оставить как есть)
RUN groupadd --gid 2000 app && \
    useradd --uid 2000 --gid 2000 -m -d /app app
USER app

# Порт и команда запуска
EXPOSE 8080
# если файл main.py и объект app = Flask(__name__)
CMD ["gunicorn", "main:app", "--bind", "0.0.0.0:8080", "--timeout", "60"]
