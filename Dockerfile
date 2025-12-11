# Dockerfile
FROM python:3.11-slim
WORKDIR /app

# 1.  OS build tools + modern pip/setuptools/wheel
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc g++ && \
    python -m pip install --upgrade pip setuptools wheel build && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# 2.  Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3.  App code
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
