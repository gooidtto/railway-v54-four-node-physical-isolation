#!/usr/bin/env python3
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

UUID = os.environ["UUID"].strip()
PRIVATE_KEY = os.environ["PRIVATE_KEY"].strip()
PUBLIC_KEY = os.environ["PUBLIC_KEY"].strip()
PUBLIC_DOMAIN = os.environ["PUBLIC_DOMAIN"].strip()

TCP_HOST = os.environ.get("RAILWAY_TCP_PROXY_DOMAIN", "").strip()
TCP_PORT_RAW = os.environ.get("RAILWAY_TCP_PROXY_PORT", "").strip()
TCP_APP_RAW = os.environ.get("RAILWAY_TCP_APPLICATION_PORT", "").strip()
if not TCP_HOST or not TCP_PORT_RAW:
    raise SystemExit("missing RAILWAY_TCP_PROXY_DOMAIN/PORT")
try:
    TCP_PORT = int(TCP_PORT_RAW)
except ValueError:
    raise SystemExit("invalid RAILWAY_TCP_PROXY_PORT")
if not 1 <= TCP_PORT <= 65535:
    raise SystemExit("invalid RAILWAY_TCP_PROXY_PORT")
try:
    TCP_APPLICATION_PORT = int(TCP_APP_RAW or "8081")
except ValueError:
    raise SystemExit("invalid RAILWAY_TCP_APPLICATION_PORT")
if TCP_APPLICATION_PORT not in (8080, 8081):
    raise SystemExit(f"unsupported Railway TCP Proxy target: {TCP_APPLICATION_PORT}; expected 8080 or 8081")

HTTP_PORT = 10086
REALITY_PORT = 10087
GATEWAY_PORTS = [8080, 8081]

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

reality_target = os.environ.get("REALITY_TARGET", "www.cloudflare.com:443")
fp = os.environ.get("REALITY_FINGERPRINT", "chrome")
xpath = os.environ.get("XHTTP_PATH", "/xhttp")
xmode = os.environ.get("XHTTP_MODE", "auto")

# Actual data flow:
#   Generate Domain :443 -> Railway HTTP proxy -> gateway:8080 -> XHTTP :10086
#   TCP Proxy :random -> gateway:${TCP_APPLICATION_PORT} -> REALITY :10087
# The public subscription MUST describe those same two external paths.
reality = {
    "tag": "vless-reality-7-sni",
    "listen": "127.0.0.1",
    "port": REALITY_PORT,
    "protocol": "vless",
    "settings": {
        "clients": [{"id": UUID, "level": 0, "flow": "xtls-rprx-vision"}],
        "decryption": "none"
    },
    "streamSettings": {
        "network": "tcp",
        "security": "reality",
        "realitySettings": {
            "show": False,
            "target": reality_target,
            "serverNames": snis,
            "privateKey": PRIVATE_KEY,
            "shortIds": [short_id]
        }
    }
}

xhttp = {
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
    "inbounds": [reality, xhttp],
    "outbounds": [
        {"tag": "direct", "protocol": "freedom"},
        {"tag": "block", "protocol": "blackhole"}
    ]
}
C.write_text(json.dumps(cfg, indent=2) + "\n")

state = {
    "schema": 3,
    "build": "fixed-8-node-unified",
    "mode": "railway-gateway-8080-8081",
    "uuid": UUID,
    "public_key": PUBLIC_KEY,
    "short_id": short_id,
    "public_domain": PUBLIC_DOMAIN,
    "tcp_proxy": [TCP_HOST, TCP_PORT],
    "tcp_application_port": TCP_APPLICATION_PORT,
    "gateway_ports": GATEWAY_PORTS,
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
        f"{urllib.parse.urlencode(params, doseq=True)}#{urllib.parse.quote(name)}"
    )

# Node 01: client TLS terminates at Railway Generate Domain. Gateway receives
# HTTP and forwards the XHTTP payload to the private security=none listener.
lines = [vless(PUBLIC_DOMAIN, 443, {
    "encryption": "none",
    "security": "tls",
    "sni": PUBLIC_DOMAIN,
    "fp": fp,
    "alpn": "h2,http/1.1",
    "type": "xhttp",
    "path": xpath,
    "mode": xmode
}, "VLESS XHTTP TLS · Railway Domain")]

# Nodes 02-08: all use the ONE Railway TCP Proxy. The SNI selects one of the
# seven REALITY serverNames; the server-side listener remains a single 10087.
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

if len(lines) != 8:
    raise SystemExit(f"subscription generation invariant failed: expected 8 nodes, got {len(lines)}")

# Write atomically so the gateway never reads a half-written subscription.
sub_tmp = D / "subscription.txt.tmp"
sub_tmp.write_text("\n".join(lines) + "\n")
os.replace(sub_tmp, D / "subscription.txt")

manifest = {
    "schema": 3,
    "build": "fixed-8-node-unified",
    "mode": "railway-gateway-8080-8081",
    "gateway": {"listen": GATEWAY_PORTS, "http_target": HTTP_PORT, "tls_target": REALITY_PORT},
    "tcp_proxy": {
        "public": [TCP_HOST, TCP_PORT],
        "application_port": TCP_APPLICATION_PORT,
        "gateway_target": TCP_APPLICATION_PORT
    },
    "nodes": {
        "https_xhttp": {
            "count": 1,
            "public": [PUBLIC_DOMAIN, 443],
            "internal": ["127.0.0.1", HTTP_PORT],
            "security": "tls at Railway / none at Xray"
        },
        "reality_7_sni": {
            "count": 7,
            "public": [TCP_HOST, TCP_PORT],
            "internal": ["127.0.0.1", REALITY_PORT],
            "sni": snis
        }
    },
    "subscription": {"file": str(D / "subscription.txt"), "node_count": len(lines)},
    "state_fingerprint": fingerprint
}
(D / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

print("BUILD=fixed-8-node-unified", flush=True)
print(f"GATEWAY_PORTS={','.join(map(str, GATEWAY_PORTS))}", flush=True)
print(f"TCP_PROXY={TCP_HOST}:{TCP_PORT} -> gateway:{TCP_APPLICATION_PORT}", flush=True)
print(f"REALITY=127.0.0.1:{REALITY_PORT} SNI_COUNT=7", flush=True)
print(f"XHTTP_TLS={PUBLIC_DOMAIN}:443 -> gateway:8080 -> 127.0.0.1:{HTTP_PORT}", flush=True)
print(f"SUBSCRIPTION_NODES={len(lines)}", flush=True)
print(f"STATE_FINGERPRINT={fingerprint}", flush=True)
