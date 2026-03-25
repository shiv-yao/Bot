import subprocess
import os
import time

api_port = os.getenv("PORT", "8000")
ui_port = "3000"

api_proc = subprocess.Popen([
    "uvicorn", "app.api:app",
    "--host", "0.0.0.0",
    "--port", api_port
])

time.sleep(2)

ui_proc = subprocess.Popen([
    "streamlit", "run", "app/dashboard.py",
    "--server.port", ui_port,
    "--server.address", "0.0.0.0"
])

try:
    api_proc.wait()
    ui_proc.wait()
except KeyboardInterrupt:
    api_proc.terminate()
    ui_proc.terminate()
