import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent

def ensure_deps():
    req = ROOT / "requirements.txt"
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(req)])

def main():
    ensure_deps()
    api = subprocess.Popen([
        sys.executable, "-m", "uvicorn", "app.api:app",
        "--host", "0.0.0.0", "--port", "8000"
    ])
    ui = subprocess.Popen([
        sys.executable, "-m", "streamlit", "run", "app/dashboard.py",
        "--server.address", "0.0.0.0",
        "--server.port", "8501",
        "--browser.gatherUsageStats", "false"
    ])
    try:
        while True:
            if api.poll() is not None or ui.poll() is not None:
                break
            time.sleep(1)
    finally:
        for p in (api, ui):
            if p.poll() is None:
                p.terminate()

if __name__ == "__main__":
    main()
