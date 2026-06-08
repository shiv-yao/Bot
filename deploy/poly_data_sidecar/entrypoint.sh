#!/bin/sh
set -eu

export POLY_DATA_SIDECAR_ROOT="${POLY_DATA_SIDECAR_ROOT:-/data/poly_data}"
export POLY_DATA_UPDATE_INTERVAL_SEC="${POLY_DATA_UPDATE_INTERVAL_SEC:-300}"
export PORT="${PORT:-8080}"

mkdir -p "$POLY_DATA_SIDECAR_ROOT/data" "$POLY_DATA_SIDECAR_ROOT/processed"

# The upstream project expects relative data/ and processed/ paths. Bind its
# generated folders to the dedicated Railway volume without modifying upstream.
rm -rf /opt/poly_data/data /opt/poly_data/processed
ln -s "$POLY_DATA_SIDECAR_ROOT/data" /opt/poly_data/data
ln -s "$POLY_DATA_SIDECAR_ROOT/processed" /opt/poly_data/processed

run_update() {
  cd /opt/poly_data
  echo "[poly_data_sidecar] starting upstream update"
  if python update.py; then
    echo "[poly_data_sidecar] upstream update completed"
  else
    echo "[poly_data_sidecar] upstream update failed; manifest service remains online" >&2
  fi
}

update_loop() {
  while true; do
    run_update
    sleep "$POLY_DATA_UPDATE_INTERVAL_SEC"
  done
}

update_loop &
exec uvicorn manifest_app:app --app-dir /opt/sidecar --host 0.0.0.0 --port "$PORT"
