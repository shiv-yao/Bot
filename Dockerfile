FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8000
CMD ["sh", "-lc", "uvicorn app.api:app --host 0.0.0.0 --port ${PORT}"]
