import json
import os
from typing import Optional

import base58
from solders.keypair import Keypair


def load_keypair() -> Optional[Keypair]:
    raw = os.getenv("PRIVATE_KEY")
    if not raw:
        return None

    raw = raw.strip()

    if raw.startswith("["):
        arr = json.loads(raw)
        return Keypair.from_bytes(bytes(arr))

    if "," in raw:
        arr = [int(x.strip()) for x in raw.split(",") if x.strip()]
        return Keypair.from_bytes(bytes(arr))

    return Keypair.from_bytes(base58.b58decode(raw))
