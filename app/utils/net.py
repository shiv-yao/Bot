import socket
import time

DNS_CACHE = {}
DNS_TTL = 60


def resolve_host(host: str):
    now = time.time()

    cached = DNS_CACHE.get(host)
    if cached and now - cached["ts"] < DNS_TTL:
        return cached["ip"]

    try:
        ip = socket.gethostbyname(host)
        DNS_CACHE[host] = {"ip": ip, "ts": now}
        print(f"DNS RESOLVE {host} -> {ip}")
        return ip
    except Exception as e:
        print("DNS FAIL:", host, repr(e))
        return None
