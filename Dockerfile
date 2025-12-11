# Dockerfile
FROM python:3.11-slim
WORKDIR /app

# OS-level deps only
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ && apt-get clean && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App + bundled model
COPY . .
ENV PYTHONPATH=/app

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
