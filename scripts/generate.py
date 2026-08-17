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


def read_proxy(prefix, default_host="", default_port="", default_app=""):
    host = os.environ.get(f"{prefix}_DOMAIN", default_host).strip()
    port_raw = os.environ.get(f"{prefix}_PORT", default_port).strip()
    app_raw = os.environ.get(f"{prefix}_APPLICATION_PORT", default_app).strip()
    if not host or not port_raw or not app_raw:
        raise SystemExit(f"missing {prefix}_DOMAIN/PORT/APPLICATION_PORT")
    try:
        port = int(port_raw)
        app = int(app_raw)
    except ValueError:
        raise SystemExit(f"invalid {prefix}_PORT/APPLICATION_PORT")
    if not 1 <= port <= 65535:
        raise SystemExit(f"invalid {prefix}_PORT")
    if app not in (8081, 8082, 8083):
        raise SystemExit(f"unsupported {prefix} target {app}; expected 8081, 8082 or 8083")
    return {"domain": host, "port": port, "application_port": app}


P1_HOST = os.environ.get("RAILWAY_TCP_PROXY_DOMAIN", "reseau.proxy.rlwy.net").strip()
P1_PORT_RAW = os.environ.get("RAILWAY_TCP_PROXY_PORT", "23337").strip()
P1_APP_RAW = os.environ.get("RAILWAY_TCP_APPLICATION_PORT", "8081").strip()
try:
    P1_PORT = int(P1_PORT_RAW)
    P1_APP = int(P1_APP_RAW)
except ValueError:
    raise SystemExit("invalid RAILWAY_TCP_PROXY_PORT/RAILWAY_TCP_APPLICATION_PORT")
if not P1_HOST or not 1 <= P1_PORT <= 65535 or P1_APP not in (8081, 8082, 8083):
    raise SystemExit("invalid primary Railway TCP Proxy settings")

p1 = {"domain": P1_HOST, "port": P1_PORT, "application_port": P1_APP}
p2 = read_proxy("RAILWAY_TCP_PROXY_2", "interchange.proxy.rlwy.net", "23389", "8082")
p3 = read_proxy("RAILWAY_TCP_PROXY_3", "altaria.proxy.rlwy.net", "17903", "8083")
proxies = [p1, p2, p3]
if len({p["application_port"] for p in proxies}) != 3:
    raise SystemExit("three TCP proxies must target distinct application ports 8081/8082/8083")

HTTP_PORT = 10086
REALITY_PORT = 10087
GATEWAY_PORTS = [8080, 8081, 8082, 8083]
NODE_COUNT = 7

sni_file = Path(os.environ.get("REALITY_SNI_CANDIDATES_FILE", "/opt/xray/config/reality-sni-candidates.txt"))
sni_values = [x.strip() for x in sni_file.read_text().splitlines() if x.strip()]
if len(sni_values) != 1:
    raise SystemExit(f"fixed baseline requires exactly 1 target-compatible REALITY SNI, got {len(sni_values)}")
reality_sni = sni_values[0]
snis = [reality_sni] * NODE_COUNT

short_file = D / "short_id.txt"
legacy_short = short_file.read_text().strip() if short_file.exists() else ""
if legacy_short and not re.fullmatch(r"[0-9a-fA-F]{2,32}", legacy_short):
    raise SystemExit("invalid legacy short_id")

shorts_file = D / "reality_short_ids.json"
short_ids = []
if shorts_file.exists():
    try:
        short_ids = json.loads(shorts_file.read_text())
    except Exception:
        short_ids = []
if not isinstance(short_ids, list):
    short_ids = []
short_ids = [str(x).strip() for x in short_ids if re.fullmatch(r"[0-9a-fA-F]{2,32}", str(x).strip())]
if legacy_short and legacy_short not in short_ids:
    short_ids.insert(0, legacy_short)
while len(short_ids) < NODE_COUNT:
    candidate = secrets.token_hex(6)
    if candidate not in short_ids:
        short_ids.append(candidate)
short_ids = short_ids[:NODE_COUNT]
shorts_file.write_text(json.dumps(short_ids, indent=2) + "\n")
short_file.write_text(short_ids[0] + "\n")

reality_target = os.environ.get("REALITY_TARGET", "www.cloudflare.com:443").strip()
if not reality_target:
    raise SystemExit("REALITY_TARGET must not be empty")
fp = os.environ.get("REALITY_FINGERPRINT", "chrome")
xpath = os.environ.get("XHTTP_PATH", "/xhttp")
xmode = os.environ.get("XHTTP_MODE", "auto")

reality = {
    "tag": "vless-reality-7-node-stable",
    "listen": "127.0.0.1",
    "port": REALITY_PORT,
    "protocol": "vless",
    "settings": {"clients": [{"id": UUID, "level": 0, "flow": "xtls-rprx-vision"}], "decryption": "none"},
    "streamSettings": {
        "network": "tcp",
        "security": "reality",
        "realitySettings": {
            "show": False,
            "target": reality_target,
            "serverNames": [reality_sni],
            "privateKey": PRIVATE_KEY,
            "shortIds": short_ids
        }
    }
}

xhttp = {
    "tag": "vless-xhttp-tls",
    "listen": "127.0.0.1",
    "port": HTTP_PORT,
    "protocol": "vless",
    "settings": {"clients": [{"id": UUID, "level": 0}], "decryption": "none"},
    "streamSettings": {"network": "xhttp", "security": "none", "xhttpSettings": {"path": xpath, "mode": xmode}}
}

cfg = {
    "log": {"loglevel": os.environ.get("XRAY_LOGLEVEL", "warning")},
    "policy": {"levels": {"0": {"handshake": 8, "connIdle": 900, "uplinkOnly": 2, "downlinkOnly": 5}}},
    "inbounds": [reality, xhttp],
    "outbounds": [{"tag": "direct", "protocol": "freedom"}, {"tag": "block", "protocol": "blackhole"}]
}
C.write_text(json.dumps(cfg, indent=2) + "\n")

state = {
    "schema": 5,
    "build": "fixed-8-node-three-tcp-stable-reality",
    "mode": "railway-gateway-8080-8081-8082-8083",
    "uuid": UUID,
    "public_key": PUBLIC_KEY,
    "short_ids": short_ids,
    "public_domain": PUBLIC_DOMAIN,
    "tcp_proxies": proxies,
    "gateway_ports": GATEWAY_PORTS,
    "reality_listener": REALITY_PORT,
    "xhttp_listener": HTTP_PORT,
    "reality_target": reality_target,
    "reality_sni": reality_sni,
}
fingerprint = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
state["fingerprint"] = fingerprint
(D / "state.json").write_text(json.dumps(state, indent=2) + "\n")


def vless(host, port, params, name):
    return f"vless://{UUID}@{host}:{port}?{urllib.parse.urlencode(params, doseq=True)}#{urllib.parse.quote(name)}"

lines = [vless(PUBLIC_DOMAIN, 443, {
    "encryption": "none", "security": "tls", "sni": PUBLIC_DOMAIN, "fp": fp,
    "alpn": "h2,http/1.1", "type": "xhttp", "path": xpath, "mode": xmode
}, "VLESS XHTTP TLS · Railway Domain")]

for i in range(1, NODE_COUNT + 1):
    proxy = proxies[0] if i <= 3 else proxies[1] if i <= 5 else proxies[2]
    sid = short_ids[i - 1]
    lines.append(vless(proxy["domain"], proxy["port"], {
        "encryption": "none", "flow": "xtls-rprx-vision", "security": "reality",
        "sni": reality_sni, "fp": fp, "pbk": PUBLIC_KEY, "sid": sid, "type": "tcp"
    }, f"VLESS REALITY Vision {i:02d} · {reality_sni} · TCP {proxy['application_port']} · SID {sid}"))

if len(lines) != 8:
    raise SystemExit(f"subscription generation invariant failed: expected 8 nodes, got {len(lines)}")

sub_tmp = D / "subscription.txt.tmp"
sub_tmp.write_text("\n".join(lines) + "\n")
os.replace(sub_tmp, D / "subscription.txt")

manifest = {
    "schema": 5,
    "build": "fixed-8-node-three-tcp-stable-reality",
    "mode": "railway-gateway-8080-8081-8082-8083",
    "gateway": {"listen": GATEWAY_PORTS, "http_target": HTTP_PORT, "tls_target": REALITY_PORT},
    "tcp_proxies": proxies,
    "nodes": {
        "https_xhttp": {"count": 1, "public": [PUBLIC_DOMAIN, 443], "internal": ["127.0.0.1", HTTP_PORT], "security": "tls at Railway / none at Xray"},
        "reality_7": {
            "count": 7,
            "distribution": [3, 2, 2],
            "internal": ["127.0.0.1", REALITY_PORT],
            "sni": reality_sni,
            "short_ids": short_ids,
            "target": reality_target,
            "public": [[p["domain"], p["port"], p["application_port"]] for p in proxies]
        }
    },
    "subscription": {"file": str(D / "subscription.txt"), "node_count": len(lines)},
    "state_fingerprint": fingerprint
}
(D / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

print("BUILD=fixed-8-node-three-tcp-stable-reality", flush=True)
print(f"GATEWAY_PORTS={','.join(map(str, GATEWAY_PORTS))}", flush=True)
for n, p in enumerate(proxies, 1):
    print(f"TCP_PROXY_{n}={p['domain']}:{p['port']} -> gateway:{p['application_port']}", flush=True)
print(f"REALITY=127.0.0.1:{REALITY_PORT} TARGET={reality_target} SNI={reality_sni} SHORT_IDS=7 DISTRIBUTION=3,2,2", flush=True)
print(f"XHTTP_TLS={PUBLIC_DOMAIN}:443 -> gateway:8080 -> 127.0.0.1:{HTTP_PORT}", flush=True)
print(f"SUBSCRIPTION_NODES={len(lines)}", flush=True)
print(f"STATE_FINGERPRINT={fingerprint}", flush=True)
