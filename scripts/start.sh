#!/bin/sh
set -eu
umask 077
BUILD_ID="stable-optional-cloudflare-ws-v2"
D="${RAILWAY_VOLUME_MOUNT_PATH:-${DATA_DIR:-/data}}"
C="${XRAY_CONFIG:-${D}/config.json}"
mkdir -p "$D" "$(dirname "$C")"

write_secret() {
  f="$1"
  v="$2"
  t="$f.tmp"
  printf '%s\n' "$v" >"$t"
  chmod 600 "$t"
  mv -f "$t" "$f"
}

PUBLIC_DOMAIN="${RAILWAY_PUBLIC_DOMAIN:-}"
[ -n "$PUBLIC_DOMAIN" ] || { echo "FATAL: RAILWAY_PUBLIC_DOMAIN unavailable" >&2; exit 1; }

TCP_HOST="${RAILWAY_TCP_PROXY_DOMAIN:-}"
TCP_PORT="${RAILWAY_TCP_PROXY_PORT:-}"
[ -n "$TCP_HOST" ] && [ -n "$TCP_PORT" ] || { echo "FATAL: one Railway TCP Proxy is required" >&2; exit 1; }

UUID_FILE="$D/uuid.txt"
PRIV_FILE="$D/reality_private_key.txt"
PUB_FILE="$D/reality_public_key.txt"
TOKEN_FILE="$D/subscription_token.txt"
CF_TOKEN_FILE="$D/cloudflare_tunnel_token.txt"

if [ -s "$UUID_FILE" ]; then
  UUID=$(tr -d '[:space:]' <"$UUID_FILE")
else
  UUID=$(xray uuid)
  write_secret "$UUID_FILE" "$UUID"
fi

if [ -s "$PRIV_FILE" ] && [ -s "$PUB_FILE" ]; then
  PRIVATE_KEY=$(tr -d '[:space:]' <"$PRIV_FILE")
  PUBLIC_KEY=$(tr -d '[:space:]' <"$PUB_FILE")
else
  OUT="$(xray x25519 2>&1)"
  PRIVATE_KEY=$(printf '%s\n' "$OUT" | awk -F': ' '/^PrivateKey/{print $2;exit}')
  PUBLIC_KEY=$(printf '%s\n' "$OUT" | awk -F': ' '/^Password/{print $2;exit}')
  [ -n "$PRIVATE_KEY" ] && [ -n "$PUBLIC_KEY" ] || { echo "FATAL: failed to generate REALITY keys" >&2; exit 1; }
  write_secret "$PRIV_FILE" "$PRIVATE_KEY"
  write_secret "$PUB_FILE" "$PUBLIC_KEY"
fi

if [ -s "$TOKEN_FILE" ]; then
  TOKEN=$(tr -d '[:space:]' <"$TOKEN_FILE")
else
  TOKEN=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')
  write_secret "$TOKEN_FILE" "$TOKEN"
fi

# Normalize Cloudflare variables once.  The generator then freezes the
# resulting state into /data/runtime.json; no later process re-evaluates ENV.
CF_TOKEN="${CLOUDFLARE_TUNNEL_TOKEN:-${CF_TUNNEL_TOKEN:-${TUNNEL_TOKEN:-}}}"
CF_ID="${CLOUDFLARE_TUNNEL_ID:-${CF_TUNNEL_ID:-${TUNNEL_ID:-}}}"
CF_HOST="${CLOUDFLARE_PUBLIC_HOSTNAME:-${CF_PUBLIC_HOSTNAME:-}}"
CF_ORIGIN="${CLOUDFLARE_ORIGIN_SERVICE:-${CF_ORIGIN_SERVICE:-}}"
CF_PORT="${WS_PORT:-${CLOUDFLARE_WS_PORT:-${CF_WS_PORT:-}}}"
CF_PATH="${WS_PATH:-${CLOUDFLARE_WS_PATH:-${CF_WS_PATH:-}}}"

export DATA_DIR="$D" XRAY_CONFIG="$C" UUID PRIVATE_KEY PUBLIC_KEY PUBLIC_DOMAIN \
  RAILWAY_TCP_PROXY_DOMAIN="$TCP_HOST" RAILWAY_TCP_PROXY_PORT="$TCP_PORT" \
  GATEWAY_PORT=8080 \
  REALITY_RAW_SNI="${REALITY_RAW_SNI:-www.cloudflare.com}" \
  REALITY_RAW_TARGET="${REALITY_RAW_TARGET:-www.cloudflare.com:443}" \
  REALITY_FINGERPRINT="${REALITY_FINGERPRINT:-chrome}" \
  REALITY_XHTTP_SNI="${REALITY_XHTTP_SNI:-www.apple.com}" \
  REALITY_XHTTP_TARGET="${REALITY_XHTTP_TARGET:-www.apple.com:443}" \
  XHTTP_PATH="${XHTTP_PATH:-/xhttp}"

export CLOUDFLARE_TUNNEL_TOKEN="$CF_TOKEN" \
  CLOUDFLARE_TUNNEL_ID="$CF_ID" \
  CLOUDFLARE_PUBLIC_HOSTNAME="$CF_HOST" \
  CLOUDFLARE_ORIGIN_SERVICE="$CF_ORIGIN" \
  WS_PORT="$CF_PORT" \
  WS_PATH="$CF_PATH"

python3 /opt/xray/scripts/generate.py

RUNTIME="$D/runtime.json"
[ -s "$RUNTIME" ] || { echo "FATAL: runtime state was not generated" >&2; exit 1; }

CF_ENABLED=$(python3 - "$RUNTIME" <<'PY'
import json,sys
s=json.load(open(sys.argv[1]));print("1" if s.get("cloudflare",{}).get("enabled") is True else "0")
PY
)
CF_HOST_STATE=$(python3 - "$RUNTIME" <<'PY'
import json,sys
print(json.load(open(sys.argv[1])).get("cloudflare",{}).get("public_hostname", ""))
PY
)
CF_ORIGIN_STATE=$(python3 - "$RUNTIME" <<'PY'
import json,sys
print(json.load(open(sys.argv[1])).get("cloudflare",{}).get("origin_service", ""))
PY
)
CF_PORT_STATE=$(python3 - "$RUNTIME" <<'PY'
import json,sys
v=json.load(open(sys.argv[1])).get("cloudflare",{}).get("ws_port");print(v if v is not None else "")
PY
)
CF_PATH_STATE=$(python3 - "$RUNTIME" <<'PY'
import json,sys
print(json.load(open(sys.argv[1])).get("cloudflare",{}).get("ws_path", ""))
PY
)
EXPECTED=$(python3 - "$RUNTIME" <<'PY'
import json,sys
print(json.load(open(sys.argv[1])).get("nodes",{}).get("count",0))
PY
)
FINGERPRINT=$(python3 - "$RUNTIME" <<'PY'
import json,sys
print(json.load(open(sys.argv[1])).get("fingerprint", ""))
PY
)

case "$EXPECTED" in
  3|4) ;;
  *) echo "FATAL: invalid runtime node count: $EXPECTED" >&2; exit 1 ;;
esac

if [ "$CF_ENABLED" = 1 ]; then
  [ -n "$CF_HOST_STATE" ] && [ -n "$CF_ORIGIN_STATE" ] && [ -n "$CF_PORT_STATE" ] && [ -n "$CF_PATH_STATE" ] || {
    echo "FATAL: runtime state says Cloudflare enabled but required state is incomplete" >&2
    exit 1
  }
  [ -n "$CF_TOKEN" ] || { echo "FATAL: runtime state says Cloudflare enabled but tunnel token is unavailable" >&2; exit 1; }
fi

echo "RUNTIME_STATE=$RUNTIME"
echo "RUNTIME_FINGERPRINT=$FINGERPRINT"
echo "CLOUDFLARE_STATE=$([ "$CF_ENABLED" = 1 ] && echo enabled || echo disabled)"
echo "CLOUDFLARE_HOST_STATE=$([ -n "$CF_HOST_STATE" ] && echo present || echo missing)"
echo "CLOUDFLARE_ORIGIN_STATE=$([ -n "$CF_ORIGIN_STATE" ] && echo present || echo missing)"
echo "CLOUDFLARE_PORT_STATE=$([ -n "$CF_PORT_STATE" ] && echo present || echo missing)"
echo "CLOUDFLARE_PATH_STATE=$([ -n "$CF_PATH_STATE" ] && echo present || echo missing)"
echo "SUBSCRIPTION_INVARIANT=$EXPECTED"

python3 - "$D/subscription.txt" "$RUNTIME" <<'PY'
import json,sys
from pathlib import Path
lines=[x.strip() for x in Path(sys.argv[1]).read_text().splitlines() if x.strip()]
runtime=json.loads(Path(sys.argv[2]).read_text())
expected=int(runtime["nodes"]["count"])
if len(lines)!=expected:
    raise SystemExit(f"FATAL: expected {expected} nodes, got {len(lines)}")
for i,line in enumerate(lines,1):
    if not line.startswith("vless://"):
        raise SystemExit(f"FATAL: invalid subscription node {i}")
    print(f"NODE_{i}={line}")
print(f"SUBSCRIPTION_COUNT={len(lines)}")
PY

xray run -test -config "$C"
xray run -config "$C" & XP=$!
GP=""
CFP=""
trap 'kill "$XP" "$GP" "$CFP" 2>/dev/null || true; wait "$XP" 2>/dev/null || true; wait "$GP" 2>/dev/null || true; wait "$CFP" 2>/dev/null || true' INT TERM EXIT

wait_port() {
  h="$1"; p="$2"; label="$3"; i=0
  while :; do
    if python3 -c 'import socket,sys;s=socket.create_connection((sys.argv[1],int(sys.argv[2])),1);s.close()' "$h" "$p" 2>/dev/null; then
      echo "READY_CHECK=$label:$p"; return 0
    fi
    if ! kill -0 "$XP" 2>/dev/null; then echo "FATAL: xray exited before $label:$p" >&2; exit 1; fi
    i=$((i+1)); [ "$i" -lt "${READY_TIMEOUT:-90}" ] || { echo "FATAL: readiness timeout $label:$p" >&2; exit 1; }
    sleep 1
  done
}

wait_http_ready() {
  url="$1"; label="$2"; i=0
  while :; do
    if python3 - "$url" <<'PY'
import sys,urllib.request
try: urllib.request.urlopen(sys.argv[1],timeout=2).read(); raise SystemExit(0)
except Exception: raise SystemExit(1)
PY
    then echo "READY_CHECK=$label"; return 0; fi
    i=$((i+1)); [ "$i" -lt "${CLOUDFLARE_READY_TIMEOUT:-45}" ] || { echo "FATAL: readiness timeout $label" >&2; exit 1; }
    sleep 1
  done
}

wait_port 127.0.0.1 10086 xhttp-http
wait_port 127.0.0.1 10087 raw-reality-vision
wait_port 127.0.0.1 10088 xhttp-reality
if [ "$CF_ENABLED" = 1 ]; then wait_port 127.0.0.1 "$CF_PORT_STATE" cloudflare-ws-origin; fi

python3 /opt/xray/scripts/gateway.py & GP=$!
wait_port 127.0.0.1 8080 protocol-router

if [ "$CF_ENABLED" = 1 ]; then
  write_secret "$CF_TOKEN_FILE" "$CF_TOKEN"
  echo "CLOUDFLARE_WS=enabled"
  echo "CLOUDFLARE_PUBLIC_HOSTNAME=$CF_HOST_STATE"
  echo "CLOUDFLARE_ORIGIN_SERVICE=$CF_ORIGIN_STATE"
  echo "CLOUDFLARE_WS_PORT=$CF_PORT_STATE"
  echo "CLOUDFLARE_WS_PATH=$CF_PATH_STATE"
  cloudflared --no-autoupdate tunnel --metrics 127.0.0.1:2000 run --token-file "$CF_TOKEN_FILE" >"$D/cloudflared.log" 2>&1 & CFP=$!
  sleep 1
  kill -0 "$CFP" 2>/dev/null || { echo "FATAL: cloudflared exited during startup" >&2; tail -n 80 "$D/cloudflared.log" >&2 || true; exit 1; }
  wait_http_ready "http://127.0.0.1:2000/ready" cloudflared-tunnel
else
  echo "CLOUDFLARE_WS=disabled"
fi

printf '%s/sub/%s\n' "https://${PUBLIC_DOMAIN}" "$TOKEN" >"$D/subscription_url.txt"
chmod 600 "$D/subscription_url.txt"

echo "RELEASE=$BUILD_ID"
echo "ARCHITECTURE=single-8080-router-plus-optional-cloudflare-tunnel"
echo "TARGET_PORT=8080"
echo "TCP=$TCP_HOST:$TCP_PORT -> 8080"
echo "ROUTES=HTTP->10086,RAW-REALITY->10087,XHTTP-REALITY->10088"
[ "$CF_ENABLED" = 1 ] && echo "ROUTES=CLOUDFLARE-WS->$CF_PORT_STATE"
echo "NODES=$EXPECTED"

while kill -0 "$XP" 2>/dev/null && kill -0 "$GP" 2>/dev/null; do
  if [ "$CF_ENABLED" = 1 ] && ! kill -0 "$CFP" 2>/dev/null; then
    echo "FATAL: cloudflared exited" >&2; tail -n 80 "$D/cloudflared.log" >&2 || true; exit 1
  fi
  sleep 5
done

echo "FATAL: supervised process exited" >&2
exit 1
