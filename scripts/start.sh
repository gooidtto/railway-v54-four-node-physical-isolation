#!/bin/sh
set -eu
umask 077

BUILD_ID="v54-four-node-physical-isolation"
D="${RAILWAY_VOLUME_MOUNT_PATH:-${DATA_DIR:-/data}}"
C="${XRAY_CONFIG:-/etc/xray/config.json}"
mkdir -p "$D" "$(dirname "$C")"

write_secret() {
  file="$1"; value="$2"; tmp="${file}.tmp"
  printf '%s\n' "$value" > "$tmp"
  chmod 600 "$tmp"
  mv -f "$tmp" "$file"
}

UUID_FILE="$D/uuid.txt"
PRIV_FILE="$D/reality_private_key.txt"
PUB_FILE="$D/reality_public_key.txt"
TOKEN_FILE="$D/subscription_token.txt"

if [ -s "$UUID_FILE" ]; then
  UUID=$(tr -d '[:space:]' < "$UUID_FILE")
else
  UUID=$(xray uuid)
  write_secret "$UUID_FILE" "$UUID"
fi

if [ -s "$PRIV_FILE" ] && [ -s "$PUB_FILE" ]; then
  PRIVATE_KEY=$(tr -d '[:space:]' < "$PRIV_FILE")
  PUBLIC_KEY=$(tr -d '[:space:]' < "$PUB_FILE")
else
  OUT="$(xray x25519 2>&1)"
  PRIVATE_KEY=$(printf '%s\n' "$OUT" | awk -F': ' '/^PrivateKey/{print $2;exit}')
  PUBLIC_KEY=$(printf '%s\n' "$OUT" | awk -F': ' '/^Password/{print $2;exit}')
  [ -n "$PRIVATE_KEY" ] && [ -n "$PUBLIC_KEY" ]
  write_secret "$PRIV_FILE" "$PRIVATE_KEY"
  write_secret "$PUB_FILE" "$PUBLIC_KEY"
fi

if [ -s "$TOKEN_FILE" ]; then
  TOKEN=$(tr -d '[:space:]' < "$TOKEN_FILE")
else
  TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
  write_secret "$TOKEN_FILE" "$TOKEN"
fi

PUBLIC_DOMAIN="${PUBLIC_DOMAIN:-${RAILWAY_PUBLIC_DOMAIN:-}}"

# Public TCP endpoints are runtime state, not persistent state. When a
# Railway API token is configured, discover all three proxies by their
# application ports (8081/8082/8083). This survives Railway changing the
# generated domains or external ports.
DISCOVERY_ENV="$D/tcp-proxy.env"
rm -f "$DISCOVERY_ENV"

if [ -n "${RAILWAY_PROJECT_TOKEN:-}" ] || [ -n "${RAILWAY_API_TOKEN:-}" ]; then
  if python3 /opt/xray/scripts/discover_tcp_proxies.py > "$DISCOVERY_ENV"; then
    # The discovery script validates hostnames/ports before emitting values.
    . "$DISCOVERY_ENV"
    echo "TCP_DISCOVERY=railway-api" 
  else
    rm -f "$DISCOVERY_ENV"
    echo "TCP_DISCOVERY=railway-api-failed; using explicit/runtime fallback" >&2
  fi
fi

# Fallbacks: 8081 is supplied automatically by Railway's generic TCP proxy
# variables. 8082/8083 can still be explicitly supplied if API discovery is
# not enabled or temporarily unavailable.
VISION_HOST="${VISION_PUBLIC_HOST:-${RAILWAY_TCP_PROXY_DOMAIN:-}}"
VISION_PORT="${VISION_PUBLIC_PORT:-${RAILWAY_TCP_PROXY_PORT:-}}"
XREAL_HOST="${XHTTP_REALITY_PUBLIC_HOST:-}"
XREAL_PORT="${XHTTP_REALITY_PUBLIC_PORT:-}"
GRPC_HOST="${GRPC_REALITY_PUBLIC_HOST:-}"
GRPC_PORT="${GRPC_REALITY_PUBLIC_PORT:-}"

[ -n "$PUBLIC_DOMAIN" ] || { echo "missing PUBLIC_DOMAIN/RAILWAY_PUBLIC_DOMAIN" >&2; exit 1; }
[ -n "$VISION_HOST" ] && [ -n "$VISION_PORT" ] || { echo "missing Vision TCP proxy: VISION_PUBLIC_HOST/PORT or RAILWAY_TCP_PROXY_DOMAIN/PORT" >&2; exit 1; }
[ -n "$XREAL_HOST" ] && [ -n "$XREAL_PORT" ] || { echo "missing XHTTP_REALITY_PUBLIC_HOST/PORT; set RAILWAY_PROJECT_TOKEN or XHTTP_REALITY_PUBLIC_*" >&2; exit 1; }
[ -n "$GRPC_HOST" ] && [ -n "$GRPC_PORT" ] || { echo "missing GRPC_REALITY_PUBLIC_HOST/PORT; set RAILWAY_PROJECT_TOKEN or GRPC_REALITY_PUBLIC_*" >&2; exit 1; }

# The old endpoint is permanently rejected.
for pair in "$VISION_HOST:$VISION_PORT" "$XREAL_HOST:$XREAL_PORT" "$GRPC_HOST:$GRPC_PORT"; do
  [ "$pair" != "altaria.proxy.rlwy.net:32227" ] || {
    echo "stale Railway TCP endpoint detected: $pair" >&2
    exit 1
  }
done

# Prevent accidental topology drift: the generic Railway TCP proxy must
# point to the dedicated Vision listener, not one of the other REALITY ports.
if [ -n "${RAILWAY_TCP_APPLICATION_PORT:-}" ] && [ "$RAILWAY_TCP_APPLICATION_PORT" != "8081" ]; then
  echo "Railway TCP Proxy system variable targets ${RAILWAY_TCP_APPLICATION_PORT}; expected 8081 for Vision" >&2
  exit 1
fi

export DATA_DIR="$D" XRAY_CONFIG="$C"
export UUID PRIVATE_KEY PUBLIC_KEY
export PUBLIC_DOMAIN
export VISION_PUBLIC_HOST="$VISION_HOST" VISION_PUBLIC_PORT="$VISION_PORT"
export XHTTP_REALITY_PUBLIC_HOST="$XREAL_HOST" XHTTP_REALITY_PUBLIC_PORT="$XREAL_PORT"
export GRPC_REALITY_PUBLIC_HOST="$GRPC_HOST" GRPC_REALITY_PUBLIC_PORT="$GRPC_PORT"

python3 /opt/xray/scripts/generate.py
xray run -test -config "$C"

python3 /opt/xray/scripts/gateway.py &
GP=$!
xray run -config "$C" &
XP=$!

trap 'kill "$XP" "$GP" 2>/dev/null || true; wait "$XP" 2>/dev/null || true; wait "$GP" 2>/dev/null || true' INT TERM EXIT

i=0
while :; do
  if kill -0 "$XP" 2>/dev/null && \
     kill -0 "$GP" 2>/dev/null && \
     python3 -c 'import socket; s=socket.create_connection(("127.0.0.1",8081),1); s.close()' 2>/dev/null && \
     python3 -c 'import socket; s=socket.create_connection(("127.0.0.1",8082),1); s.close()' 2>/dev/null && \
     python3 -c 'import socket; s=socket.create_connection(("127.0.0.1",8083),1); s.close()' 2>/dev/null && \
     python3 -c 'import socket; s=socket.create_connection(("127.0.0.1",10086),1); s.close()' 2>/dev/null; then
    break
  fi
  i=$((i+1))
  [ "$i" -lt "${READY_TIMEOUT:-90}" ] || { echo "runtime readiness timeout" >&2; exit 1; }
  sleep 1
done

printf '%s/sub/%s\n' "https://${PUBLIC_DOMAIN}" "$TOKEN" > "$D/subscription_url.txt"
chmod 600 "$D/subscription_url.txt"

echo "BUILD=$BUILD_ID"
echo "TOPOLOGY=8080/http-gateway 8081/vision 8082/xhttp-reality 8083/grpc-reality 10086/xhttp-tls"
echo "VISION_PUBLIC=${VISION_HOST}:${VISION_PORT}"
echo "XHTTP_REALITY_PUBLIC=${XREAL_HOST}:${XREAL_PORT}"
echo "GRPC_REALITY_PUBLIC=${GRPC_HOST}:${GRPC_PORT}"
echo "SUBSCRIPTION=https://${PUBLIC_DOMAIN}/sub/${TOKEN}"
echo "READY: build=$BUILD_ID http=8080 vision=8081 xhttp-reality=8082 grpc-reality=8083 xhttp-tls=10086"

while kill -0 "$XP" 2>/dev/null && kill -0 "$GP" 2>/dev/null; do
  sleep 5
done

echo "supervised process exited" >&2
exit 1
