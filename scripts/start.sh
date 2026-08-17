#!/bin/sh
set -eu
umask 077
BUILD_ID="fixed-single-8080-router-v2"
D="${RAILWAY_VOLUME_MOUNT_PATH:-${DATA_DIR:-/data}}";C="${XRAY_CONFIG:-/etc/xray/config.json}"
mkdir -p "$D" "$(dirname "$C")"
write_secret(){ f="$1";v="$2";t="$f.tmp";printf '%s\n' "$v">"$t";chmod 600 "$t";mv -f "$t" "$f"; }
PUBLIC_DOMAIN="${RAILWAY_PUBLIC_DOMAIN:-}";[ -n "$PUBLIC_DOMAIN" ]||{ echo "FATAL: RAILWAY_PUBLIC_DOMAIN unavailable" >&2;exit 1; }
# Railway injects one canonical TCP proxy pair. Additional proxies are explicit service variables.
TCP_HOST="${RAILWAY_TCP_PROXY_DOMAIN:-shuttle.proxy.rlwy.net}";TCP_PORT="${RAILWAY_TCP_PROXY_PORT:-50612}"
TCP2_HOST="${TCP_PROXY_2_DOMAIN:-${RAILWAY_TCP_PROXY_2_DOMAIN:-interchange.proxy.rlwy.net}}";TCP2_PORT="${TCP_PROXY_2_PORT:-${RAILWAY_TCP_PROXY_2_PORT:-23389}}"
TCP3_HOST="${TCP_PROXY_3_DOMAIN:-${RAILWAY_TCP_PROXY_3_DOMAIN:-altaria.proxy.rlwy.net}}";TCP3_PORT="${TCP_PROXY_3_PORT:-${RAILWAY_TCP_PROXY_3_PORT:-17903}}"
UUID_FILE="$D/uuid.txt";PRIV_FILE="$D/reality_private_key.txt";PUB_FILE="$D/reality_public_key.txt";TOKEN_FILE="$D/subscription_token.txt"
if [ -s "$UUID_FILE" ];then UUID=$(tr -d '[:space:]'<"$UUID_FILE");else UUID=$(xray uuid);write_secret "$UUID_FILE" "$UUID";fi
if [ -s "$PRIV_FILE" ]&&[ -s "$PUB_FILE" ];then PRIVATE_KEY=$(tr -d '[:space:]'<"$PRIV_FILE");PUBLIC_KEY=$(tr -d '[:space:]'<"$PUB_FILE");else OUT="$(xray x25519 2>&1)";PRIVATE_KEY=$(printf '%s\n' "$OUT"|awk -F': ' '/^PrivateKey/{print $2;exit}');PUBLIC_KEY=$(printf '%s\n' "$OUT"|awk -F': ' '/^Password/{print $2;exit}');[ -n "$PRIVATE_KEY" ]&&[ -n "$PUBLIC_KEY" ]||{ echo "FATAL: failed to generate REALITY keys" >&2;exit 1;};write_secret "$PRIV_FILE" "$PRIVATE_KEY";write_secret "$PUB_FILE" "$PUBLIC_KEY";fi
if [ -s "$TOKEN_FILE" ];then TOKEN=$(tr -d '[:space:]'<"$TOKEN_FILE");else TOKEN=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))');write_secret "$TOKEN_FILE" "$TOKEN";fi
# Keep UUID/REALITY keys/short IDs persistent; only generated subscription metadata is rebuilt.
rm -f "$D/subscription.txt" "$D/subscription.txt.tmp" "$D/manifest.json" "$D/state.json"
WS_CERT="$D/ws_tls_cert.pem";WS_KEY="$D/ws_tls_key.pem";WS_HOST="${WS_HOST:-$TCP3_HOST}"
if [ ! -s "$WS_CERT" ]||[ ! -s "$WS_KEY" ]||! grep -q "^$WS_HOST$" "$D/ws_tls_cert_meta" 2>/dev/null;then openssl req -x509 -newkey rsa:2048 -nodes -days 825 -keyout "$WS_KEY" -out "$WS_CERT" -subj "/CN=$WS_HOST" -addext "subjectAltName=DNS:$WS_HOST" >/dev/null 2>&1;printf '%s\n' "$WS_HOST">"$D/ws_tls_cert_meta";chmod 600 "$WS_CERT" "$WS_KEY";fi
export DATA_DIR="$D" XRAY_CONFIG="$C" UUID PRIVATE_KEY PUBLIC_KEY PUBLIC_DOMAIN \
RAILWAY_TCP_PROXY_DOMAIN="$TCP_HOST" RAILWAY_TCP_PROXY_PORT="$TCP_PORT" \
TCP_PROXY_2_DOMAIN="$TCP2_HOST" TCP_PROXY_2_PORT="$TCP2_PORT" RAILWAY_TCP_PROXY_2_DOMAIN="$TCP2_HOST" RAILWAY_TCP_PROXY_2_PORT="$TCP2_PORT" \
TCP_PROXY_3_DOMAIN="$TCP3_HOST" TCP_PROXY_3_PORT="$TCP3_PORT" RAILWAY_TCP_PROXY_3_DOMAIN="$TCP3_HOST" RAILWAY_TCP_PROXY_3_PORT="$TCP3_PORT" \
GATEWAY_PORT=8080 REALITY_RAW_SNI="${REALITY_RAW_SNI:-www.cloudflare.com}" REALITY_GRPC_SNI="${REALITY_GRPC_SNI:-www.apple.com}" \
WS_CERT WS_KEY WS_HOST
python3 /opt/xray/scripts/generate.py
python3 - "$D/subscription.txt" <<'PY'
import sys
from pathlib import Path
lines=[x.strip() for x in Path(sys.argv[1]).read_text().splitlines() if x.strip()]
if len(lines)!=4:raise SystemExit(f"FATAL: expected exactly 4 nodes, got {len(lines)}")
for i,line in enumerate(lines,1): print(f"NODE_{i}={line}")
PY
xray run -test -config "$C"
xray run -config "$C" & XP=$!;GP="";trap 'kill "$XP" "$GP" 2>/dev/null||true;wait "$XP" 2>/dev/null||true;wait "$GP" 2>/dev/null||true' INT TERM EXIT
wait_port(){ h="$1";p="$2";label="$3";i=0;while :;do if python3 -c 'import socket,sys;s=socket.create_connection((sys.argv[1],int(sys.argv[2])),1);s.close()' "$h" "$p" 2>/dev/null;then echo "READY_CHECK=$label:$p";return 0;fi;if ! kill -0 "$XP" 2>/dev/null;then echo "FATAL: xray exited before $label:$p" >&2;exit 1;fi;i=$((i+1));[ "$i" -lt "${READY_TIMEOUT:-90}" ]||{ echo "FATAL: readiness timeout $label:$p" >&2;exit 1;};sleep 1;done;}
wait_port 127.0.0.1 10086 xhttp;wait_port 127.0.0.1 10087 reality-vision;wait_port 127.0.0.1 10088 reality-grpc;wait_port 127.0.0.1 10089 ws-tls
python3 /opt/xray/scripts/gateway.py & GP=$!
wait_port 127.0.0.1 8080 protocol-router
printf '%s/sub/%s\n' "https://${PUBLIC_DOMAIN}" "$TOKEN">"$D/subscription_url.txt";chmod 600 "$D/subscription_url.txt"
echo "RELEASE=$BUILD_ID";echo "ARCHITECTURE=single-8080-protocol-router";echo "SUBSCRIPTION_INVARIANT=4";echo "TARGET_PORT=8080";echo "TCP1=$TCP_HOST:$TCP_PORT -> 8080";echo "TCP2=$TCP2_HOST:$TCP2_PORT -> 8080";echo "TCP3=$TCP3_HOST:$TCP3_PORT -> 8080";echo "NODES=4"
while kill -0 "$XP" 2>/dev/null&&kill -0 "$GP" 2>/dev/null;do sleep 5;done;echo "FATAL: supervised process exited" >&2;exit 1
