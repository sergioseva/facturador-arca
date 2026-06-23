FROM python:3.12-slim

WORKDIR /app

# Dependencias (gunicorn se agrega solo para servir en producción)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Código (los secretos y la base van montados, no copiados — ver compose)
COPY app.py afip.py db.py ./
COPY templates ./templates

EXPOSE 5001

# 1 worker: el cache del token WSAA y la base SQLite son archivos locales.
# timeout alto porque las llamadas a ARCA pueden tardar varios segundos.
CMD ["gunicorn", "-w", "1", "-b", "0.0.0.0:5001", "--timeout", "120", "app:app"]
