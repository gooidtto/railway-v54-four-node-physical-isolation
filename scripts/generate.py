#!/usr/bin/env python3
import base64
import hashlib
import json
import os
import re
import secrets
import urllib.parse
from pathlib import Path

D = Path(os.environ.get("DATA_DIR", "/data"))
D.mkdir(parents=True, exist_ok=True)
C = Path(os.environ.get("XRAY_CONFIG", "/etc/xray/config.json"))

UUID = os.environ["UUID"]
PRIVATE_KEY = os.environ["PRIVATE_KEY"]
PUBLIC_KEY = os.environ["PUBLIC_KEY"]
PUBLIC_DOMAIN = os.environ["PUBLIC_DOMAIN"]

TCP_HOST = os.environ.get("RAILWAY_TCP_PROXY_DOMAIN", "").strip()
TCP_PORT_RAW = os.environ.get("RAILWAY_TCP_PROXY_PORT", "").strip()
if not TCP_HOST or not TCP_PORT_RAW:
    raise SystemExit("missing RAILWAY_TCP_PROXY_DOMAIN/PORT")
try:
    TCP_PORT = int(TCP_PORT_RAW)
except ValueError:
    raise SystemExit("invalid RAILWAY_TCP_PROXY_PORT")
if not 1 <= TCP_PORT <= 65535:
    raise SystemExit("invalid RAILWAY_TCP_PROXY_PORT")

HTTP_PORT = 10086
REALITY_PORT = 10087
GATEWAY_PORT = 8080

sni_file = Path(os.environ.get(
    "REALITY_SNI_CANDIDATES_FILE",
    "/opt/xray/config/reality-sni-candidates.txt"
))
snis = [x.strip() for x in sni_file.read_text().splitlines() if x.strip()]
if len(snis) != 7:
    raise SystemExit(f"fixed baseline requires exactly 7 REALITY SNI entries, got {len(snis)}")

short_file = D / "short_id.txt"
short_id = short_file.read_text().strip() if short_file.exists() else secrets.token_hex(6)
if not re.fullmatch(r"[0-9a-fA-F]{2,32}", short_id):
    raise SystemExit("invalid short_id")

target = os.environ.get("REALITY_TARGET", "www.cloudflare.com:443")
fp = os.environ.get("REALITY_FINGERPRINT", "chrome")
xpath = os.environ.get("XHTTP_PATH", "/xhttp")
xmode = os.environ.get("XHTTP_MODE", "auto")

# One REALITY inbound serves seven SNI profiles. The external TCP Proxy is
# intentionally a single endpoint and the gateway transparently forwards TLS
# ClientHello traffic to 10087. The Railway Generate Domain HTTP path is
# forwarded to the private XHTTP listener at 10086.
reality = {
    "tag": "vless-reality-7-sni",
    "listen": "127.0.0.1",
    "port": REALITY_PORT,
    "protocol": "vless",
    "settings": {
        "clients": [{
            "id": UUID,
            "level": 0,
            "flow": "xtls-rprx-vision"
        }],
        "decryption": "none"
    },
    "streamSettings": {
        "network": "tcp",
        "security": "reality",
        "realitySettings": {
            "show": False,
            "target": target,
            "serverNames": snis,
            "privateKey": PRIVATE_KEY,
            "shortIds": [short_id]
        }
    }
}

xhttp_tls = {
    "tag": "vless-xhttp-tls",
    "listen": "127.0.0.1",
    "port": HTTP_PORT,
    "protocol": "vless",
    "settings": {
        "clients": [{"id": UUID, "level": 0}],
        "decryption": "none"
    },
    "streamSettings": {
        "network": "xhttp",
        "security": "none",
        "xhttpSettings": {"path": xpath, "mode": xmode}
    }
}

cfg = {
    "log": {"loglevel": os.environ.get("XRAY_LOGLEVEL", "warning")},
    "policy": {"levels": {"0": {
        "handshake": 8,
        "connIdle": 900,
        "uplinkOnly": 2,
        "downlinkOnly": 5
    }}},
    "inbounds": [reality, xhttp_tls],
    "outbounds": [
        {"tag": "direct", "protocol": "freedom"},
        {"tag": "block", "protocol": "blackhole"}
    ]
}
C.write_text(json.dumps(cfg, indent=2) + "\n")

state = {
    "schema": 2,
    "build": "fixed-8-node-baseline",
    "mode": "single-tcp-proxy-7-reality-plus-https-xhttp",
    "uuid": UUID,
    "public_key": PUBLIC_KEY,
    "short_id": short_id,
    "public_domain": PUBLIC_DOMAIN,
    "tcp_proxy": [TCP_HOST, TCP_PORT],
    "reality_listener": REALITY_PORT,
    "xhttp_listener": HTTP_PORT,
    "reality_sni": snis,
}
fingerprint = hashlib.sha256(
    json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
state["fingerprint"] = fingerprint
(D / "state.json").write_text(json.dumps(state, indent=2) + "\n")
short_file.write_text(short_id + "\n")

def vless(host, port, params, name):
    return (
        f"vless://{UUID}@{host}:{port}?"
        f"{urllib.parse.urlencode(params)}#{urllib.parse.quote(name)}"
    )

lines = [
    vless(PUBLIC_DOMAIN, 443, {
        "encryption": "none",
        "security": "tls",
        "sni": PUBLIC_DOMAIN,
        "fp": fp,
        "type": "xhttp",
        "path": xpath,
        "mode": xmode
    }, "VLESS XHTTP TLS"),
]

for i, sni in enumerate(snis, 1):
    lines.append(vless(TCP_HOST, TCP_PORT, {
        "encryption": "none",
        "flow": "xtls-rprx-vision",
        "security": "reality",
        "sni": sni,
        "fp": fp,
        "pbk": PUBLIC_KEY,
        "sid": short_id,
        "type": "tcp"
    }, f"VLESS REALITY Vision {i:02d} · {sni}"))

(D / "subscription.txt").write_text("\n".join(lines) + "\n")

manifest = {
    "schema": 2,
    "build": "fixed-8-node-baseline",
    "mode": "single-tcp-proxy-7-reality-plus-https-xhttp",
    "gateway": "0.0.0.0:8080",
    "tcp_proxy": {"public": [TCP_HOST, TCP_PORT], "target": GATEWAY_PORT},
    "nodes": {
        "https_xhttp": {"public": [PUBLIC_DOMAIN, 443], "internal": ["127.0.0.1", HTTP_PORT]},
        "reality_7_sni": {"public": [TCP_HOST, TCP_PORT], "internal": ["127.0.0.1", REALITY_PORT], "sni": snis}
    },
    "state_fingerprint": fingerprint
}
(D / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

print("BUILD=fixed-8-node-baseline", flush=True)
print(f"TCP_PROXY={TCP_HOST}:{TCP_PORT} -> gateway:{GATEWAY_PORT}", flush=True)
print(f"REALITY=127.0.0.1:{REALITY_PORT} SNI_COUNT=7", flush=True)
print(f"XHTTP_TLS={PUBLIC_DOMAIN}:443 -> 127.0.0.1:{HTTP_PORT}", flush=True)
print("NODES=8 (1 HTTPS XHTTP + 7 REALITY Vision SNI)", flush=True)
