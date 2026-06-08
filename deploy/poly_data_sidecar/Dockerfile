FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POLY_DATA_SIDECAR_ROOT=/data/poly_data

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt
RUN git clone --depth 1 https://github.com/warproxxx/poly_data.git /opt/poly_data
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -e /opt/poly_data fastapi uvicorn

COPY deploy/poly_data_sidecar/manifest_app.py /opt/sidecar/manifest_app.py
COPY deploy/poly_data_sidecar/entrypoint.sh /opt/sidecar/entrypoint.sh
RUN chmod +x /opt/sidecar/entrypoint.sh

WORKDIR /opt/poly_data
EXPOSE 8080
CMD ["/opt/sidecar/entrypoint.sh"]
