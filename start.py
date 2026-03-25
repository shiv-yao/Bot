
import os
import sys
import subprocess

def main():
    port = os.environ.get("PORT", "8000")
    subprocess.check_call([
        sys.executable, "-m", "uvicorn",
        "app.api:app",
        "--host", "0.0.0.0",
        "--port", port
    ])

if __name__ == "__main__":
    main()
