#!/usr/bin/env python3
import hashlib, json, os, re, secrets, urllib.parse
from pathlib import Path

D = Path(os.environ.get("DATA_DIR", "/data"))
D.mkdir(parents=True, exist_ok=True)
C = Path(os.environ.get("XRAY_CONFIG", "/etc/xray/config.json"))

UUID = os.environ["UUID"]
PRIVATE_KEY = os.environ["PRIVATE_KEY"]
PUBLIC_KEY = os.environ["PUBLIC_KEY"]
PUBLIC_DOMAIN = os.environ["PUBLIC_DOMAIN"]

VISION_HOST = os.environ["VISION_PUBLIC_HOST"]
VISION_PORT = int(os.environ["VISION_PUBLIC_PORT"])
XREAL_HOST = os.environ["XHTTP_REALITY_PUBLIC_HOST"]
XREAL_PORT = int(os.environ["XHTTP_REALITY_PUBLIC_PORT"])
GRPC_HOST = os.environ["GRPC_REALITY_PUBLIC_HOST"]
GRPC_PORT = int(os.environ["GRPC_REALITY_PUBLIC_PORT"])

VISION = int(os.environ.get("XRAY_VISION_PORT", "8081"))
XREAL = int(os.environ.get("XRAY_XHTTP_REALITY_PORT", "8082"))
GRPC = int(os.environ.get("XRAY_GRPC_REALITY_PORT", "8083"))
HTTP = int(os.environ.get("XRAY_HTTP_PORT", "10086"))

if (VISION, XREAL, GRPC, HTTP) != (8081, 8082, 8083, 10086):
    raise SystemExit("internal topology must be 8081/8082/8083/10086")

for label, host, port in (
    ("VISION", VISION_HOST, VISION_PORT),
    ("XHTTP_REALITY", XREAL_HOST, XREAL_PORT),
    ("GRPC_REALITY", GRPC_HOST, GRPC_PORT),
):
    if not host or not 1 <= port <= 65535:
        raise SystemExit(f"invalid {label} public endpoint")

# Railway TCP Proxy endpoints are runtime configuration.
# Never restore host/port from persisted state.
stale = ("altaria.proxy.rlwy.net", 32227)
for host, port in ((VISION_HOST, VISION_PORT), (XREAL_HOST, XREAL_PORT), (GRPC_HOST, GRPC_PORT)):
    if (host, port) == stale:
        raise SystemExit("refusing stale endpoint altaria.proxy.rlwy.net:32227")

sni_file = Path(os.environ.get(
    "REALITY_SNI_CANDIDATES_FILE",
    "/opt/xray/config/reality-sni-candidates.txt"
))
snis = [x.strip() for x in sni_file.read_text().splitlines() if x.strip()]
if len(snis) < 3:
    raise SystemExit("need at least three REALITY SNI candidates")

short_file = D / "short_id.txt"
short_id = short_file.read_text().strip() if short_file.exists() else secrets.token_hex(6)
if not re.fullmatch(r"[0-9a-fA-F]{2,32}", short_id):
    raise SystemExit("invalid short_id")

target = os.environ.get("REALITY_TARGET", "www.cloudflare.com:443")
fp = os.environ.get("REALITY_FINGERPRINT", "chrome")
xpath = os.environ.get("XHTTP_PATH", "/xhttp")
xmode = os.environ.get("XHTTP_MODE", "auto")
grpc_name = os.environ.get("GRPC_SERVICE_NAME", "grpc-service")

def inbound(port, sni, network, flow=None):
    client = {"id": UUID, "level": 0}
    if flow:
        client["flow"] = flow
    stream = {
        "network": network,
        "security": "reality",
        "realitySettings": {
            "show": False,
            "target": target,
            "serverNames": [sni],
            "privateKey": PRIVATE_KEY,
            "shortIds": [short_id]
        }
    }
    if network == "xhttp":
        stream["xhttpSettings"] = {"path": xpath, "mode": xmode}
    elif network == "grpc":
        stream["grpcSettings"] = {"serviceName": grpc_name}
    return {
        "tag": f"vless-{network}-{port}",
        "listen": "0.0.0.0",
        "port": port,
        "protocol": "vless",
        "settings": {"clients": [client], "decryption": "none"},
        "streamSettings": stream
    }

inbounds = [
    inbound(VISION, snis[0], "raw", "xtls-rprx-vision"),
    inbound(XREAL, snis[1], "xhttp"),
    inbound(GRPC, snis[2], "grpc"),
    {
        "tag": "xhttp-tls",
        "listen": "127.0.0.1",
        "port": HTTP,
        "protocol": "vless",
        "settings": {"clients": [{"id": UUID, "level": 0}], "decryption": "none"},
        "streamSettings": {
            "network": "xhttp",
            "security": "none",
            "xhttpSettings": {"path": xpath, "mode": xmode}
        }
    }
]

cfg = {
    "log": {"loglevel": os.environ.get("XRAY_LOGLEVEL", "warning")},
    "policy": {"levels": {"0": {
        "handshake": 8,
        "connIdle": 900,
        "uplinkOnly": 2,
        "downlinkOnly": 5
    }}},
    "inbounds": inbounds,
    "outbounds": [
        {"tag": "direct", "protocol": "freedom"},
        {"tag": "block", "protocol": "blackhole"}
    ]
}
C.write_text(json.dumps(cfg, indent=2) + "\n")

state = {
    "schema": 1,
    "build": "v54-four-node-physical-isolation",
    "mode": "physical-isolation",
    "uuid": UUID,
    "public_key": PUBLIC_KEY,
    "short_id": short_id,
    "public_domain": PUBLIC_DOMAIN,
    "vision": [VISION_HOST, VISION_PORT, VISION],
    "xhttp_reality": [XREAL_HOST, XREAL_PORT, XREAL],
    "xhttp_tls": [PUBLIC_DOMAIN, 443, HTTP],
    "grpc_reality": [GRPC_HOST, GRPC_PORT, GRPC],
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
    vless(VISION_HOST, VISION_PORT, {
        "encryption": "none",
        "flow": "xtls-rprx-vision",
        "security": "reality",
        "sni": snis[0],
        "fp": fp,
        "pbk": PUBLIC_KEY,
        "sid": short_id,
        "type": "tcp"
    }, "VLESS RAW REALITY Vision"),

    vless(XREAL_HOST, XREAL_PORT, {
        "encryption": "none",
        "security": "reality",
        "sni": snis[1],
        "fp": fp,
        "pbk": PUBLIC_KEY,
        "sid": short_id,
        "type": "xhttp",
        "path": xpath,
        "mode": xmode
    }, "VLESS XHTTP REALITY"),

    vless(PUBLIC_DOMAIN, 443, {
        "encryption": "none",
        "security": "tls",
        "sni": PUBLIC_DOMAIN,
        "fp": fp,
        "type": "xhttp",
        "path": xpath,
        "mode": xmode
    }, "VLESS XHTTP TLS"),

    vless(GRPC_HOST, GRPC_PORT, {
        "encryption": "none",
        "security": "reality",
        "sni": snis[2],
        "fp": fp,
        "pbk": PUBLIC_KEY,
        "sid": short_id,
        "type": "grpc",
        "serviceName": grpc_name
    }, "VLESS gRPC REALITY")
]
(D / "subscription.txt").write_text("\n".join(lines) + "\n")

manifest = {
    "schema": 1,
    "build": "v54-four-node-physical-isolation",
    "mode": "physical-isolation",
    "gateway": "0.0.0.0:8080",
    "nodes": {
        "vision": {"public": [VISION_HOST, VISION_PORT], "internal": ["127.0.0.1", VISION]},
        "xhttp_reality": {"public": [XREAL_HOST, XREAL_PORT], "internal": ["127.0.0.1", XREAL]},
        "xhttp_tls": {"public": [PUBLIC_DOMAIN, 443], "internal": ["127.0.0.1", HTTP]},
        "grpc_reality": {"public": [GRPC_HOST, GRPC_PORT], "internal": ["127.0.0.1", GRPC]}
    },
    "state_fingerprint": fingerprint
}
(D / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

print("BUILD=v54-four-node-physical-isolation", flush=True)
print(f"VISION={VISION_HOST}:{VISION_PORT} -> 127.0.0.1:{VISION}", flush=True)
print(f"XHTTP_REALITY={XREAL_HOST}:{XREAL_PORT} -> 127.0.0.1:{XREAL}", flush=True)
print(f"XHTTP_TLS={PUBLIC_DOMAIN}:443 -> 127.0.0.1:{HTTP}", flush=True)
print(f"GRPC_REALITY={GRPC_HOST}:{GRPC_PORT} -> 127.0.0.1:{GRPC}", flush=True)
