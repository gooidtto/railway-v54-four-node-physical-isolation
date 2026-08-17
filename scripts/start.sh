#!/bin/sh
set -eu
umask 077
BUILD_ID="fixed-4-node-physical-isolation-v4"
D="${RAILWAY_VOLUME_MOUNT_PATH:-${DATA_DIR:-/data}}"
C="${XRAY_CONFIG:-/etc/xray/config.json}"
READY_FILE="${GATEWAY_READY_FILE:-$D/gateway.ready}"
mkdir -p "$D" "$(dirname "$C")"
rm -f "$READY_FILE"

write_secret(){ f="$1"; v="$2"; t="$f.tmp"; printf '%s\n' "$v" > "$t"; chmod 600 "$t"; mv -f "$t" "$f"; }

PUBLIC_DOMAIN="${RAILWAY_PUBLIC_DOMAIN:-}"
[ -n "$PUBLIC_DOMAIN" ] || { echo "ERROR: RAILWAY_PUBLIC_DOMAIN unavailable" >&2; exit 1; }

# TCP Proxy values are runtime-only. Never fall back to old/default public endpoints.
TCP_HOST="${RAILWAY_TCP_PROXY_DOMAIN:-}"
TCP_PORT="${RAILWAY_TCP_PROXY_PORT:-}"
TCP_APP="${RAILWAY_TCP_APPLICATION_PORT:-}"
TCP2_HOST="${RAILWAY_TCP_PROXY_2_DOMAIN:-}"
TCP2_PORT="${RAILWAY_TCP_PROXY_2_PORT:-}"
TCP2_APP="${RAILWAY_TCP_PROXY_2_APPLICATION_PORT:-}"
TCP3_HOST="${RAILWAY_TCP_PROXY_3_DOMAIN:-}"
TCP3_PORT="${RAILWAY_TCP_PROXY_3_PORT:-}"
TCP3_APP="${RAILWAY_TCP_PROXY_3_APPLICATION_PORT:-}"
[ -n "$TCP_HOST" ] && [ -n "$TCP_PORT" ] && [ -n "$TCP_APP" ] && [ -n "$TCP2_HOST" ] && [ -n "$TCP2_PORT" ] && [ -n "$TCP2_APP" ] && [ -n "$TCP3_HOST" ] && [ -n "$TCP3_PORT" ] && [ -n "$TCP3_APP" ] || { echo "ERROR: all three Railway TCP Proxy runtime variables are required" >&2; exit 1; }
[ "$TCP_APP:$TCP2_APP:$TCP3_APP" = "8081:8082:8083" ] || { echo "ERROR: TCP targets must be 8081:8082:8083" >&2; exit 1; }

UUID_FILE="$D/uuid.txt"; PRIV_FILE="$D/reality_private_key.txt"; PUB_FILE="$D/reality_public_key.txt"; TOKEN_FILE="$D/subscription_token.txt"
if [ -s "$UUID_FILE" ]; then UUID=$(tr -d '[:space:]' < "$UUID_FILE"); else UUID=$(xray uuid); write_secret "$UUID_FILE" "$UUID"; fi
if [ -s "$PRIV_FILE" ] && [ -s "$PUB_FILE" ]; then PRIVATE_KEY=$(tr -d '[:space:]' < "$PRIV_FILE"); PUBLIC_KEY=$(tr -d '[:space:]' < "$PUB_FILE"); else OUT="$(xray x25519 2>&1)"; PRIVATE_KEY=$(printf '%s\n' "$OUT"|awk -F': ' '/^PrivateKey/{print $2;exit}'); PUBLIC_KEY=$(printf '%s\n' "$OUT"|awk -F': ' '/^Password/{print $2;exit}'); [ -n "$PRIVATE_KEY" ] && [ -n "$PUBLIC_KEY"]; write_secret "$PRIV_FILE" "$PRIVATE_KEY"; write_secret "$PUB_FILE" "$PUBLIC_KEY"; fi
if [ -s "$TOKEN_FILE" ]; then TOKEN=$(tr -d '[:space:]' < "$TOKEN_FILE"); else TOKEN=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))'); write_secret "$TOKEN_FILE" "$TOKEN"; fi

rm -f "$D/subscription.txt" "$D/subscription.txt.tmp" "$D/manifest.json" "$D/state.json" "$D/reality_short_ids.json"
WS_CERT="$D/ws_tls_cert.pem"; WS_KEY="$D/ws_tls_key.pem"; WS_HOST="${WS_HOST:-$TCP3_HOST}"
if [ ! -s "$WS_CERT" ] || [ ! -s "$WS_KEY" ] || ! grep -q "^$WS_HOST$" "$D/ws_tls_cert_meta" 2>/dev/null; then openssl req -x509 -newkey rsa:2048 -nodes -days 825 -keyout "$WS_KEY" -out "$WS_CERT" -subj "/CN=$WS_HOST" -addext "subjectAltName=DNS:$WS_HOST" >/dev/null 2>&1; printf '%s\n' "$WS_HOST" > "$D/ws_tls_cert_meta"; chmod 600 "$WS_CERT" "$WS_KEY"; fi

export DATA_DIR="$D" XRAY_CONFIG="$C" UUID PRIVATE_KEY PUBLIC_KEY PUBLIC_DOMAIN
export RAILWAY_TCP_PROXY_DOMAIN="$TCP_HOST" RAILWAY_TCP_PROXY_PORT="$TCP_PORT" RAILWAY_TCP_APPLICATION_PORT="$TCP_APP"
export RAILWAY_TCP_PROXY_2_DOMAIN="$TCP2_HOST" RAILWAY_TCP_PROXY_2_PORT="$TCP2_PORT" RAILWAY_TCP_PROXY_2_APPLICATION_PORT="$TCP2_APP"
export RAILWAY_TCP_PROXY_3_DOMAIN="$TCP3_HOST" RAILWAY_TCP_PROXY_3_PORT="$TCP3_PORT" RAILWAY_TCP_PROXY_3_APPLICATION_PORT="$TCP3_APP"
export XRAY_HTTP_PORT=10086 XRAY_REALITY_PORT=10087 GATEWAY_PORTS="8080,8081,8082,8083" GATEWAY_READY_FILE="$READY_FILE"
export REALITY_SNI_CANDIDATES_FILE="${REALITY_SNI_CANDIDATES_FILE:-/opt/xray/config/reality-sni-candidates.txt}" WS_CERT WS_KEY WS_HOST

python3 /opt/xray/scripts/generate.py
python3 - "$D/subscription.txt" "$BUILD_ID" <<'PY'
import sys
p=sys.argv[1]; build=sys.argv[2]
lines=[x.strip() for x in open(p,encoding='utf-8') if x.strip()]
if len(lines)!=4: raise SystemExit(f"FATAL: subscription invariant failed: expected 4 nodes, got {len(lines)}")
print(f"BUILD={build}",flush=True); print("SUBSCRIPTION_INVARIANT=4",flush=True)
PY

xray run -test -config "$C"
xray run -config "$C" & XP=$!; GP=""; trap 'rm -f "$READY_FILE"; kill "$XP" "$GP" 2>/dev/null || true; wait "$XP" 2>/dev/null || true; wait "$GP" 2>/dev/null || true' INT TERM EXIT
wait_port(){ h="$1"; p="$2"; label="$3"; i=0; while :; do if python3 -c 'import socket,sys;s=socket.create_connection((sys.argv[1],int(sys.argv[2])),1);s.close()' "$h" "$p" 2>/dev/null; then echo "READY_CHECK=$label:$p"; return 0; fi; if ! kill -0 "$XP" 2>/dev/null; then echo "ERROR: xray exited before $label:$p" >&2; exit 1; fi; i=$((i+1)); [ "$i" -lt "${READY_TIMEOUT:-90}" ] || { echo "ERROR: readiness timeout $label:$p" >&2; exit 1; }; sleep 1; done; }
wait_port 127.0.0.1 10086 xray-xhttp; wait_port 127.0.0.1 10087 xray-reality-raw; wait_port 127.0.0.1 10088 xray-reality-grpc; wait_port 127.0.0.1 10089 xray-ws-tls
python3 /opt/xray/scripts/gateway.py & GP=$!
wait_port 127.0.0.1 8080 gateway; wait_port 127.0.0.1 8081 gateway; wait_port 127.0.0.1 8082 gateway; wait_port 127.0.0.1 8083 gateway

python3 - "$D/subscription.txt" "$PUBLIC_DOMAIN" <<'PY'
import sys,urllib.parse
from pathlib import Path
lines=[x.strip() for x in Path(sys.argv[1]).read_text(encoding='utf-8').splitlines() if x.strip()]
public=sys.argv[2]; assert len(lines)==4
u=[urllib.parse.urlsplit(x) for x in lines]
assert u[0].hostname==public and u[0].port==443
for x in u[1:]: assert x.hostname and x.port
PY

printf '%s/sub/%s\n' "https://${PUBLIC_DOMAIN}" "$TOKEN" > "$D/subscription_url.txt"; chmod 600 "$D/subscription_url.txt"; printf 'ready\n' > "$READY_FILE"; chmod 600 "$READY_FILE"
echo "RELEASE=$BUILD_ID"; echo "RAILWAY_PUBLIC_DOMAIN=$PUBLIC_DOMAIN"; echo "SUBSCRIPTION_INVARIANT=4"; echo "8080 -> 10086 VLESS XHTTP TLS"; echo "8081 -> 10087 VLESS RAW REALITY Vision"; echo "8082 -> 10088 VLESS gRPC REALITY"; echo "8083 -> 10089 VLESS WS TLS"; echo "TCP1=${TCP_HOST}:${TCP_PORT}"; echo "TCP2=${TCP2_HOST}:${TCP2_PORT}"; echo "TCP3=${TCP3_HOST}:${TCP3_PORT}"; echo "NODES=4"
while kill -0 "$XP" 2>/dev/null && kill -0 "$GP" 2>/dev/null; do sleep 5; done; echo "ERROR: supervised process exited" >&2; exit 1
