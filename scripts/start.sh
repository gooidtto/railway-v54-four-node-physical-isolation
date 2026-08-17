#!/bin/sh
set -eu
umask 077

BUILD_ID="fixed-8-node-three-tcp-dynamic-railway-domain-v4"
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

# Railway runtime is the single source of truth for the public Web hostname.
# Never hardcode the hostname and never fall back to a persisted/custom value.
PUBLIC_DOMAIN="${RAILWAY_PUBLIC_DOMAIN:-}"
[ -n "$PUBLIC_DOMAIN" ] || { echo "ERROR: RAILWAY_PUBLIC_DOMAIN is unavailable; refusing to generate a guessed hostname" >&2; exit 1; }

UUID_FILE="$D/uuid.txt"
PRIV_FILE="$D/reality_private_key.txt"
PUB_FILE="$D/reality_public_key.txt"
TOKEN_FILE="$D/subscription_token.txt"

if [ -s "$UUID_FILE" ]; then UUID=$(tr -d '[:space:]' < "$UUID_FILE"); else UUID=$(xray uuid); write_secret "$UUID_FILE" "$UUID"; fi
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
if [ -s "$TOKEN_FILE" ]; then TOKEN=$(tr -d '[:space:]' < "$TOKEN_FILE"); else TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))'); write_secret "$TOKEN_FILE" "$TOKEN"; fi

TCP_HOST="${RAILWAY_TCP_PROXY_DOMAIN:-reseau.proxy.rlwy.net}"
TCP_PORT="${RAILWAY_TCP_PROXY_PORT:-23337}"
TCP_APP="${RAILWAY_TCP_APPLICATION_PORT:-8081}"
TCP2_HOST="${RAILWAY_TCP_PROXY_2_DOMAIN:-interchange.proxy.rlwy.net}"
TCP2_PORT="${RAILWAY_TCP_PROXY_2_PORT:-23389}"
TCP2_APP="${RAILWAY_TCP_PROXY_2_APPLICATION_PORT:-8082}"
TCP3_HOST="${RAILWAY_TCP_PROXY_3_DOMAIN:-altaria.proxy.rlwy.net}"
TCP3_PORT="${RAILWAY_TCP_PROXY_3_PORT:-17903}"
TCP3_APP="${RAILWAY_TCP_PROXY_3_APPLICATION_PORT:-8083}"

case "$TCP_APP:$TCP2_APP:$TCP3_APP" in
  8081:8082:8083) : ;;
  *) echo "ERROR: TCP targets must be 8081:8082:8083; got $TCP_APP:$TCP2_APP:$TCP3_APP" >&2; exit 1 ;;
esac

export DATA_DIR="$D" XRAY_CONFIG="$C"
export UUID PRIVATE_KEY PUBLIC_KEY PUBLIC_DOMAIN
export RAILWAY_TCP_PROXY_DOMAIN="$TCP_HOST" RAILWAY_TCP_PROXY_PORT="$TCP_PORT" RAILWAY_TCP_APPLICATION_PORT="$TCP_APP"
export RAILWAY_TCP_PROXY_2_DOMAIN="$TCP2_HOST" RAILWAY_TCP_PROXY_2_PORT="$TCP2_PORT" RAILWAY_TCP_PROXY_2_APPLICATION_PORT="$TCP2_APP"
export RAILWAY_TCP_PROXY_3_DOMAIN="$TCP3_HOST" RAILWAY_TCP_PROXY_3_PORT="$TCP3_PORT" RAILWAY_TCP_PROXY_3_APPLICATION_PORT="$TCP3_APP"
export XRAY_HTTP_PORT=10086 XRAY_REALITY_PORT=10087
export GATEWAY_PORTS="8080,8081,8082,8083"
export GATEWAY_READY_FILE="$READY_FILE"
export REALITY_SNI_CANDIDATES_FILE="${REALITY_SNI_CANDIDATES_FILE:-/opt/xray/config/reality-sni-candidates.txt}"

python3 /opt/xray/scripts/generate.py
xray run -test -config "$C"

# Verify the generated state uses exactly the runtime Railway hostname.
python3 - "$D/state.json" "$PUBLIC_DOMAIN" <<'PY'
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
expected = sys.argv[2]
actual = state.get("public_domain")
if actual != expected:
    raise SystemExit(f"PUBLIC_DOMAIN_MISMATCH generated={actual!r} runtime={expected!r}")
PY

a=0
xray run -config "$C" &
XP=$!
GP=""
trap 'rm -f "$READY_FILE"; kill "$XP" "$GP" 2>/dev/null || true; wait "$XP" 2>/dev/null || true; wait "$GP" 2>/dev/null || true' INT TERM EXIT

wait_port() {
  host="$1"; port="$2"; label="$3"; i=0
  while :; do
    if python3 -c 'import socket,sys; s=socket.create_connection((sys.argv[1],int(sys.argv[2])),1); s.close()' "$host" "$port" 2>/dev/null; then
      echo "READY_CHECK=$label:$port"; return 0
    fi
    if ! kill -0 "$XP" 2>/dev/null; then echo "ERROR: xray exited before $label:$port became ready" >&2; exit 1; fi
    i=$((i+1)); [ "$i" -lt "${READY_TIMEOUT:-90}" ] || { echo "ERROR: readiness timeout waiting for $label:$port" >&2; exit 1; }; sleep 1
  done
}

wait_port 127.0.0.1 10086 xray-xhttp
wait_port 127.0.0.1 10087 xray-reality
python3 /opt/xray/scripts/gateway.py &
GP=$!
wait_port 127.0.0.1 8080 gateway
wait_port 127.0.0.1 8081 gateway
wait_port 127.0.0.1 8082 gateway
wait_port 127.0.0.1 8083 gateway

printf '%s/sub/%s\n' "https://${PUBLIC_DOMAIN}" "$TOKEN" > "$D/subscription_url.txt"
chmod 600 "$D/subscription_url.txt"
printf 'ready\n' > "$READY_FILE"
chmod 600 "$READY_FILE"

i=0
while :; do
  if python3 -c 'import urllib.request; r=urllib.request.urlopen("http://127.0.0.1:8080/ready", timeout=2); raise SystemExit(0 if r.status == 200 and r.read() == b"ready\\n" else 1)' 2>/dev/null; then break; fi
  i=$((i+1)); [ "$i" -lt "${READY_TIMEOUT:-90}" ] || { echo "ERROR: gateway /ready verification failed" >&2; exit 1; }; sleep 1
done

echo "BUILD=$BUILD_ID"
echo "RAILWAY_PUBLIC_DOMAIN=$PUBLIC_DOMAIN (DYNAMIC RUNTIME VALUE)"
echo "TCP_PROXY_1=${TCP_HOST}:${TCP_PORT} -> ${TCP_APP}"
echo "TCP_PROXY_2=${TCP2_HOST}:${TCP2_PORT} -> ${TCP2_APP}"
echo "TCP_PROXY_3=${TCP3_HOST}:${TCP3_PORT} -> ${TCP3_APP}"
echo "GATEWAY_LISTEN=8080,8081,8082,8083"
echo "REALITY_SNI=www.cloudflare.com SHORT_IDS=7 DISTRIBUTION=3,2,2"
echo "SUBSCRIPTION=https://${PUBLIC_DOMAIN}/sub/${TOKEN}"
echo "NODES=8 (1 HTTPS XHTTP + 7 REALITY Vision; TCP 3,2,2; unique short IDs)"
echo "READY: build=$BUILD_ID gateway=8080,8081,8082,8083 xray_reality=10087 xray_xhttp=10086"

while kill -0 "$XP" 2>/dev/null && kill -0 "$GP" 2>/dev/null; do sleep 5; done
echo "ERROR: supervised process exited" >&2
exit 1
