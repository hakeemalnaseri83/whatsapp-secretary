# Deployment for Render (free stable host for the WhatsApp voice secretary)
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code (whapi_whatsapp needs config + llm)
COPY config.py llm.py whapi_whatsapp.py ./

# Render provides the PORT env var
ENV PORT=8000

EXPOSE $PORT

CMD ["sh", "-c", "uvicorn whapi_whatsapp:app --host 0.0.0.0 --port ${PORT}"]
