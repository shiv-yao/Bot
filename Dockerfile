FROM python:3.12-slim
LABEL app.rebuild="btc5m-v4-hardened-20260603-1"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN pip install --no-cache-dir -e .
EXPOSE 8080
CMD ["python", "-m", "polymarket_latency_bot.btc5m_event_main_v4"]
