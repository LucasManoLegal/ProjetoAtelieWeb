# syntax=docker/dockerfile:1

FROM python:3.11-slim

# Evita .pyc e garante logs sem buffer
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependências de sistema (compilação de libs e SQLite)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*

# Instala dependências Python primeiro (cache de camadas)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o restante do código da aplicação
COPY . .

# Garante que a pasta de dados exista (bancos SQLite)
RUN mkdir -p /app/data

EXPOSE 5000

# Usuário não-root por segurança
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

# Sobe a aplicação Flask com Gunicorn (produção)
# --preload: importa o app uma única vez no processo mestre antes de criar os
# workers, evitando que rotinas de inicialização (ex: criação de usuário padrão)
# rodem em paralelo em cada worker e causem condição de corrida no SQLite.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", "--worker-class", "gthread", "--timeout", "120", "--preload", "app:app"]