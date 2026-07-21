FROM python:3.11-slim

WORKDIR /app/ai-core

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Botがまだ出来上がっていない間、コンテナが落ちないよう待機させる設定
CMD ["python", "-c", "import time; print('ai-core container ready.'); time.sleep(86400)"]
