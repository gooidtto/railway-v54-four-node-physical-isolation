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
APP_PORT = 8080

TCP_HOST = (os.environ.get("RAILWAY_TCP_PROXY_DOMAIN") or "").strip()
TCP_PORT_RAW = (os.environ.get("RAILWAY_TCP_PROXY_PORT") or "").strip()
if not TCP_HOST or not TCP_PORT_RAW:
    raise SystemExit("FATAL: RAILWAY_TCP_PROXY_DOMAIN/PORT required")
try:
    TCP_PORT = int(TCP_PORT_RAW)
except ValueError:
    raise SystemExit("FATAL: invalid RAILWAY_TCP_PROXY_PORT")
if not 1 <= TCP_PORT <= 65535:
    raise SystemExit("FATAL: invalid TCP proxy port")

FP = os.environ.get("REALITY_FINGERPRINT", "chrome").strip() or "chrome"
RAW_SNI = os.environ.get("REALITY_RAW_SNI", "www.cloudflare.com").strip() or "www.cloudflare.com"
XHTTP_SNI = os.environ.get("REALITY_XHTTP_SNI", "www.apple.com").strip() or "www.apple.com"
RAW_TARGET = os.environ.get("REALITY_RAW_TARGET", "www.cloudflare.com:443").strip() or "www.cloudflare.com:443"
XHTTP_TARGET = os.environ.get("REALITY_XHTTP_TARGET", "www.apple.com:443").strip() or "www.apple.com:443"
XPATH = os.environ.get("XHTTP_PATH", "/xhttp").strip() or "/xhttp"


def env_first(*names):
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


CF_TOKEN = env_first("CLOUDFLARE_TUNNEL_TOKEN", "CF_TUNNEL_TOKEN", "TUNNEL_TOKEN")
CF_HOST = env_first("CLOUDFLARE_PUBLIC_HOSTNAME", "CF_PUBLIC_HOSTNAME").lower()
CF_ORIGIN_RAW = env_first("CLOUDFLARE_ORIGIN_SERVICE", "CF_ORIGIN_SERVICE")
CF_PORT_RAW = env_first("WS_PORT", "CLOUDFLARE_WS_PORT", "CF_WS_PORT")
CF_PATH = env_first("WS_PATH", "CLOUDFLARE_WS_PATH", "CF_WS_PATH")
CF_ID = env_first("CLOUDFLARE_TUNNEL_ID", "CF_TUNNEL_ID", "TUNNEL_ID")

# Base deployment = 3 nodes. A complete Cloudflare variable set enables node 4.
CF_ENABLED = bool(CF_TOKEN and CF_HOST and CF_PORT_RAW and CF_PATH)
CF_PORT = None
CF_INVALID_REASON = ""
CF_ORIGIN = ""

if CF_ENABLED:
    try:
        CF_PORT = int(CF_PORT_RAW)
    except ValueError:
        CF_ENABLED = False
        CF_INVALID_REASON = "WS_PORT is not an integer"
    if CF_ENABLED and not 1 <= CF_PORT <= 65535:
        CF_ENABLED = False
        CF_INVALID_REASON = "WS_PORT outside 1-65535"
    if CF_ENABLED and CF_PORT == APP_PORT:
        CF_ENABLED = False
        CF_INVALID_REASON = "WS_PORT must differ from GATEWAY_PORT 8080"
    if CF_ENABLED and CF_PORT in (10086, 10087, 10088):
        CF_ENABLED = False
        CF_INVALID_REASON = "WS_PORT conflicts with an existing Xray inbound port"
    if CF_ENABLED and not re.fullmatch(r"[A-Za-z0-9.-]+", CF_HOST):
        CF_ENABLED = False
        CF_INVALID_REASON = "CLOUDFLARE_PUBLIC_HOSTNAME invalid"
    if CF_ENABLED and not CF_PATH.startswith("/"):
        CF_ENABLED = False
        CF_INVALID_REASON = "WS_PATH must start with /"
    if CF_ENABLED:
        CF_ORIGIN = f"http://127.0.0.1:{CF_PORT}"

ids_file = D / "reality_short_ids.json"
try:
    ids = json.loads(ids_file.read_text()) if ids_file.exists() else []
except Exception:
    ids = []
ids = [str(x) for x in ids if re.fullmatch(r"[0-9a-fA-F]{2,32}", str(x))]
while len(ids) < 2:
    ids.append(secrets.token_hex(6))
ids = ids[:2]
ids_file.write_text(json.dumps(ids, indent=2) + "\n")


def reality(tag, port, network, sni, target, sid, flow=""):
    client = {"id": UUID, "level": 0}
    if flow:
        client["flow"] = flow
    ss = {
        "network": network,
        "security": "reality",
        "realitySettings": {
            "show": False,
            "target": target,
            "serverNames": [sni],
            "privateKey": PRIVATE_KEY,
            "shortIds": [sid],
        },
    }
    if network == "xhttp":
        ss["xhttpSettings"] = {"path": XPATH, "mode": "auto"}
    return {
        "tag": tag,
        "listen": "127.0.0.1",
        "port": port,
        "protocol": "vless",
        "settings": {"clients": [client], "decryption": "none"},
        "streamSettings": ss,
    }


xhttp_tls = {
    "tag": "vless-xhttp-tls",
    "listen": "127.0.0.1",
    "port": 10086,
    "protocol": "vless",
    "settings": {"clients": [{"id": UUID, "level": 0}], "decryption": "none"},
    "streamSettings": {
        "network": "xhttp",
        "security": "none",
        "xhttpSettings": {"path": XPATH, "mode": "auto"},
    },
}

raw = reality("vless-reality-vision", 10087, "tcp", RAW_SNI, RAW_TARGET, ids[0], "xtls-rprx-vision")
xhttp_reality = reality("vless-xhttp-reality", 10088, "xhttp", XHTTP_SNI, XHTTP_TARGET, ids[1])
inbounds = [xhttp_tls, raw, xhttp_reality]

if CF_ENABLED:
    inbounds.append({
        "tag": "vless-ws-cloudflare",
        "listen": "127.0.0.1",
        "port": CF_PORT,
        "protocol": "vless",
        "settings": {"clients": [{"id": UUID, "level": 0}], "decryption": "none"},
        "streamSettings": {
            "network": "ws",
            "security": "none",
            "wsSettings": {"path": CF_PATH},
        },
    })

config = {
    "log": {"loglevel": os.environ.get("XRAY_LOGLEVEL", "warning")},
    "policy": {"levels": {"0": {"handshake": 8, "connIdle": 900, "uplinkOnly": 2, "downlinkOnly": 5}}},
    "inbounds": inbounds,
    "outbounds": [{"tag": "direct", "protocol": "freedom"}, {"tag": "block", "protocol": "blackhole"}],
}
C.write_text(json.dumps(config, indent=2) + "\n")


def q(d):
    return urllib.parse.urlencode({k: str(v) for k, v in d.items() if v not in (None, "")}, safe="")


def link(host, port, params, name):
    return f'vless://{UUID}@{host}:{port}?{q(params)}#{urllib.parse.quote(name, safe="")}'


lines = [
    link(PUBLIC_DOMAIN, 443, {
        "encryption": "none", "security": "tls", "sni": PUBLIC_DOMAIN,
        "fp": FP, "alpn": "h2,http/1.1", "type": "xhttp", "path": XPATH, "mode": "auto",
    }, "VLESS XHTTP TLS · Railway Domain"),
    link(TCP_HOST, TCP_PORT, {
        "encryption": "none", "flow": "xtls-rprx-vision", "security": "reality",
        "sni": RAW_SNI, "fp": FP, "pbk": PUBLIC_KEY, "sid": ids[0], "type": "tcp",
    }, "VLESS RAW REALITY Vision · TCP Proxy"),
    link(TCP_HOST, TCP_PORT, {
        "encryption": "none", "security": "reality", "sni": XHTTP_SNI,
        "fp": FP, "alpn": "h2", "pbk": PUBLIC_KEY, "sid": ids[1],
        "type": "xhttp", "path": XPATH, "mode": "auto",
    }, "VLESS XHTTP REALITY · TCP Proxy"),
]

if CF_ENABLED:
    lines.append(link(CF_HOST, 443, {
        "encryption": "none", "security": "tls", "sni": CF_HOST, "fp": FP,
        "alpn": "http/1.1", "type": "ws", "host": CF_HOST, "path": CF_PATH,
    }, "VLESS WS TLS · Cloudflare Tunnel"))

NODE_COUNT = len(lines)
if NODE_COUNT not in (3, 4):
    raise SystemExit(f"FATAL: invalid node count: {NODE_COUNT}")

runtime = {
    "schema": 22,
    "build": "stable-optional-cloudflare-ws-v4",
    "architecture": "single-8080-router-plus-optional-cloudflare-tunnel",
    "cloudflare": {
        "enabled": CF_ENABLED,
        "token_configured": bool(CF_TOKEN),
        "tunnel_id_configured": bool(CF_ID),
        "public_hostname": CF_HOST if CF_ENABLED else "",
        "origin_service": CF_ORIGIN if CF_ENABLED else "",
        "origin_service_input_present": bool(CF_ORIGIN_RAW),
        "ws_port": CF_PORT if CF_ENABLED else None,
        "ws_path": CF_PATH if CF_ENABLED else "",
        "validation_error": CF_INVALID_REASON,
    },
    "nodes": {
        "count": NODE_COUNT,
        "distribution": {
            "01": "domain-xhttp-tls",
            "02": "raw-reality-vision",
            "03": "xhttp-reality",
            **({"04": "cloudflare-ws-tls"} if CF_ENABLED else {}),
        },
    },
    "application_port": APP_PORT,
    "public_domain": PUBLIC_DOMAIN,
    "tcp_proxy": {"domain": TCP_HOST, "port": TCP_PORT, "application_port": APP_PORT},
    "routes": {
        "domain_xhttp_tls": {"port": 10086},
        "raw_reality_vision": {"sni": RAW_SNI, "port": 10087, "short_id": ids[0]},
        "xhttp_reality": {"sni": XHTTP_SNI, "port": 10088, "short_id": ids[1]},
        **({"cloudflare_ws_tls": {"host": CF_HOST, "port": CF_PORT, "path": CF_PATH}} if CF_ENABLED else {}),
    },
}
runtime["fingerprint"] = hashlib.sha256(json.dumps(runtime, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

(D / "runtime.json").write_text(json.dumps(runtime, indent=2) + "\n")
(D / "state.json").write_text(json.dumps(runtime, indent=2) + "\n")
(D / "subscription.txt.tmp").write_text("\n".join(lines) + "\n")
os.replace(D / "subscription.txt.tmp", D / "subscription.txt")

manifest = {
    "schema": 22,
    "build": "stable-optional-cloudflare-ws-v4",
    "node_count": NODE_COUNT,
    "application_port": APP_PORT,
    "cloudflare_ws_enabled": CF_ENABLED,
    "distribution": runtime["nodes"]["distribution"],
    "runtime_fingerprint": runtime["fingerprint"],
}
(D / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

print("RELEASE=stable-optional-cloudflare-ws-v4", flush=True)
print("RUNTIME_STATE=/data/runtime.json", flush=True)
print(f"RUNTIME_FINGERPRINT={runtime['fingerprint']}", flush=True)
print(f"CLOUDFLARE_WS={'enabled' if CF_ENABLED else 'disabled'}", flush=True)
print(f"CF_ENV_TOKEN={'present' if CF_TOKEN else 'missing'}", flush=True)
print(f"CF_ENV_HOST={'present' if CF_HOST else 'missing'}", flush=True)
print(f"CF_ENV_ORIGIN={'present' if CF_ORIGIN_RAW else 'missing'}", flush=True)
print(f"CF_ENV_PORT={'present' if CF_PORT_RAW else 'missing'}", flush=True)
print(f"CF_ENV_PATH={'present' if CF_PATH else 'missing'}", flush=True)
print(f"CF_ENV_TUNNEL_ID={'present' if CF_ID else 'missing'}", flush=True)
if CF_INVALID_REASON:
    print(f"CLOUDFLARE_VALIDATION={CF_INVALID_REASON}", flush=True)
if CF_ENABLED:
    print(f"CLOUDFLARE_ORIGIN_NORMALIZED={CF_ORIGIN}", flush=True)
print(f"SUBSCRIPTION_INVARIANT={NODE_COUNT}", flush=True)
print(f"DOMAIN {PUBLIC_DOMAIN}:443 -> 8080 -> 10086 XHTTP TLS", flush=True)
print(f"TCP {TCP_HOST}:{TCP_PORT} -> 8080 -> {RAW_SNI} -> 10087 RAW REALITY Vision", flush=True)
print(f"TCP {TCP_HOST}:{TCP_PORT} -> 8080 -> {XHTTP_SNI} -> 10088 XHTTP REALITY", flush=True)
if CF_ENABLED:
    print(f"CF {CF_HOST}:443 -> tunnel -> {CF_ORIGIN} -> {CF_PORT} WS TLS", flush=True)
print(f"NODES={NODE_COUNT}", flush=True)
