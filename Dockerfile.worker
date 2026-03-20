FROM python:3.12-slim

WORKDIR /app

# System deps for curl_cffi and native packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libffi-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for layer caching
COPY worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire app
COPY . .

# Copy grok_core from dr_nick (bundled)
# This gets copied at build time from the repo
COPY worker/grok_core/ /app/grok_core/

# Copy deepseek4free dsk module (bundled)
COPY worker/dsk/ /app/dsk/

EXPOSE 8080

CMD ["gunicorn", "-b", "0.0.0.0:8080", "-w", "1", "--threads", "4", "--timeout", "900", "worker.app:app"]
