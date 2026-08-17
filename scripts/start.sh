#!/bin/sh
set -eu
umask 077

BUILD_ID="fixed-8-node-unified-readiness-v2"
D="${RAILWAY_VOLUME_MOUNT_PATH:-${DATA_DIR:-/data}}"
C="${XRAY_CONFIG:-/etc/xray/config.json}"
READY_FILE="${GATEWAY_READY_FILE:-$D/gateway.ready}"
mkdir -p "$D" "$(dirname "$C")"
rm -f "$READY_FILE"

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
TCP_HOST="${RAILWAY_TCP_PROXY_DOMAIN:-}"
TCP_PORT="${RAILWAY_TCP_PROXY_PORT:-}"
TCP_APP="${RAILWAY_TCP_APPLICATION_PORT:-}"

[ -n "$PUBLIC_DOMAIN" ] || { echo "missing RAILWAY_PUBLIC_DOMAIN" >&2; exit 1; }
[ -n "$TCP_HOST" ] && [ -n "$TCP_PORT" ] || { echo "missing RAILWAY_TCP_PROXY_DOMAIN/PORT" >&2; exit 1; }

case "$TCP_APP" in
  8080|8081) : ;;
  *) echo "unsupported Railway TCP Proxy target: ${TCP_APP:-unset}; expected 8080 or 8081" >&2; exit 1 ;;
esac

export DATA_DIR="$D" XRAY_CONFIG="$C"
export UUID PRIVATE_KEY PUBLIC_KEY PUBLIC_DOMAIN
export RAILWAY_TCP_PROXY_DOMAIN="$TCP_HOST" RAILWAY_TCP_PROXY_PORT="$TCP_PORT"
export RAILWAY_TCP_APPLICATION_PORT="$TCP_APP"
export XRAY_HTTP_PORT=10086 XRAY_REALITY_PORT=10087
export GATEWAY_PORTS="8080,8081"
export GATEWAY_READY_FILE="$READY_FILE"
export REALITY_SNI_CANDIDATES_FILE="${REALITY_SNI_CANDIDATES_FILE:-/opt/xray/config/reality-sni-candidates.txt}"

python3 /opt/xray/scripts/generate.py
xray run -test -config "$C"

# Start Xray first. Railway readiness must not be exposed until both private
# inbounds and the public gateway are alive and the subscription is generated.
xray run -config "$C" &
XP=$!

trap 'rm -f "$READY_FILE"; kill "$XP" "$GP" 2>/dev/null || true; wait "$XP" 2>/dev/null || true; wait "$GP" 2>/dev/null || true' INT TERM EXIT

wait_port() {
  host="$1"
  port="$2"
  label="$3"
  i=0
  while :; do
    if python3 -c 'import socket,sys; s=socket.create_connection((sys.argv[1],int(sys.argv[2])),1); s.close()' "$host" "$port" 2>/dev/null; then
      echo "READY_CHECK=$label:$port"
      return 0
    fi
    if ! kill -0 "$XP" 2>/dev/null; then
      echo "xray exited before $label:$port became ready" >&2
      exit 1
    fi
    i=$((i+1))
    [ "$i" -lt "${READY_TIMEOUT:-90}" ] || { echo "readiness timeout waiting for $label:$port" >&2; exit 1; }
    sleep 1
  done
}

wait_port 127.0.0.1 10086 xray-xhttp
wait_port 127.0.0.1 10087 xray-reality

python3 /opt/xray/scripts/gateway.py &
GP=$!

wait_port 127.0.0.1 8080 gateway
wait_port 127.0.0.1 8081 gateway

printf '%s/sub/%s\n' "https://${PUBLIC_DOMAIN}" "$TOKEN" > "$D/subscription_url.txt"
chmod 600 "$D/subscription_url.txt"

# Only this marker turns /ready from 503 into 200.
printf 'ready\n' > "$READY_FILE"
chmod 600 "$READY_FILE"

# Verify the actual HTTP readiness contract before announcing READY.
i=0
while :; do
  if python3 -c 'import urllib.request; r=urllib.request.urlopen("http://127.0.0.1:8080/ready", timeout=2); raise SystemExit(0 if r.status == 200 and r.read() == b"ready\\n" else 1)' 2>/dev/null; then
    break
  fi
  i=$((i+1))
  [ "$i" -lt "${READY_TIMEOUT:-90}" ] || { echo "gateway /ready verification failed" >&2; exit 1; }
  sleep 1
done

echo "BUILD=$BUILD_ID"
echo "TOPOLOGY=GenerateDomain:443->gateway:8080->xhttp:10086; TCP:${TCP_HOST}:${TCP_PORT}->gateway:${TCP_APP}->reality:10087"
echo "TCP_PROXY=${TCP_HOST}:${TCP_PORT} -> ${TCP_APP}"
echo "GATEWAY_LISTEN=8080,8081"
echo "REALITY_SNI_COUNT=7"
echo "SUBSCRIPTION=https://${PUBLIC_DOMAIN}/sub/${TOKEN}"
echo "NODES=8 (1 HTTPS XHTTP + 7 REALITY Vision SNI)"
echo "READY: build=$BUILD_ID gateway=8080,8081 xray_reality=10087 xray_xhttp=10086 tcp_proxy=${TCP_HOST}:${TCP_PORT} target=${TCP_APP}"

after_ready_wait=0
while kill -0 "$XP" 2>/dev/null && kill -0 "$GP" 2>/dev/null; do
  sleep 5
done

echo "supervised process exited" >&2
exit 1
