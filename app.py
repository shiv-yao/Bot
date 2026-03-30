"""Compatibility entrypoint.

Starts the real FastAPI app in main.py.
The uploaded demo app was preserved under legacy/app_demo_uploaded.py and is
not used for production trading.
"""

import os
import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
