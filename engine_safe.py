# engine_safe.py

def safe_slice(x, n=3):
    if isinstance(x, (list, tuple)):
        return x[:n]
    return []

def ensure_list(x):
    if isinstance(x, list):
        return x
    return []

def ensure_dict(x):
    if isinstance(x, dict):
        return x
    return {}

def safe_get(d, key, default=None):
    if isinstance(d, dict):
        return d.get(key, default)
    return default
