# Dockerfile
FROM python:3.11-slim
WORKDIR /app


# ---- pre-install Vosk small English model ----
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget unzip && \
    wget -q https://alphacephei.com/vosk/models/vosk-model-small-en-0.15.zip && \
    unzip -q vosk-model-small-en-0.15.zip && \
    rm vosk-model-small-en-0.15.zip && \
    apt-get clean && rm -rf /var/lib/apt/lists/*
    
# 2.  Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3.  App code
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
