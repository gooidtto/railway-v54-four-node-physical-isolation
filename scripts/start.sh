#!/bin/sh
set -eu
umask 077
BUILD_ID="stable-3node-single-8080"
D="${RAILWAY_VOLUME_MOUNT_PATH:-${DATA_DIR:-/data}}";C="${XRAY_CONFIG:-/etc/xray/config.json}"
mkdir -p "$D" "$(dirname "$C")"
write_secret(){ f="$1";v="$2";t="$f.tmp";printf '%s\n' "$v">"$t";chmod 600 "$t";mv -f "$t" "$f"; }
PUBLIC_DOMAIN="${RAILWAY_PUBLIC_DOMAIN:-}";[ -n "$PUBLIC_DOMAIN" ]||{ echo "FATAL: RAILWAY_PUBLIC_DOMAIN unavailable" >&2;exit 1; }
TCP_HOST="${RAILWAY_TCP_PROXY_DOMAIN:-}";TCP_PORT="${RAILWAY_TCP_PROXY_PORT:-}"
[ -n "$TCP_HOST" ]&&[ -n "$TCP_PORT" ]||{ echo "FATAL: one Railway TCP Proxy is required" >&2;exit 1; }
UUID_FILE="$D/uuid.txt";PRIV_FILE="$D/reality_private_key.txt";PUB_FILE="$D/reality_public_key.txt";TOKEN_FILE="$D/subscription_token.txt"
if [ -s "$UUID_FILE" ];then UUID=$(tr -d '[:space:]'<"$UUID_FILE");else UUID=$(xray uuid);write_secret "$UUID_FILE" "$UUID";fi
if [ -s "$PRIV_FILE" ]&&[ -s "$PUB_FILE" ];then PRIVATE_KEY=$(tr -d '[:space:]'<"$PRIV_FILE");PUBLIC_KEY=$(tr -d '[:space:]'<"$PUB_FILE");else OUT="$(xray x25519 2>&1)";PRIVATE_KEY=$(printf '%s\n' "$OUT"|awk -F': ' '/^PrivateKey/{print $2;exit}');PUBLIC_KEY=$(printf '%s\n' "$OUT"|awk -F': ' '/^Password/{print $2;exit}');[ -n "$PRIVATE_KEY" ]&&[ -n "$PUBLIC_KEY" ]||{ echo "FATAL: failed to generate REALITY keys" >&2;exit 1;};write_secret "$PRIV_FILE" "$PRIVATE_KEY";write_secret "$PUB_FILE" "$PUBLIC_KEY";fi
if [ -s "$TOKEN_FILE" ];then TOKEN=$(tr -d '[:space:]'<"$TOKEN_FILE");else TOKEN=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))');write_secret "$TOKEN_FILE" "$TOKEN";fi
rm -f "$D/subscription.txt" "$D/subscription.txt.tmp" "$D/manifest.json" "$D/state.json"
export DATA_DIR="$D" XRAY_CONFIG="$C" UUID PRIVATE_KEY PUBLIC_KEY PUBLIC_DOMAIN \
RAILWAY_TCP_PROXY_DOMAIN="$TCP_HOST" RAILWAY_TCP_PROXY_PORT="$TCP_PORT" \
GATEWAY_PORT=8080 REALITY_RAW_SNI="${REALITY_RAW_SNI:-www.cloudflare.com}" \
REALITY_RAW_TARGET="${REALITY_RAW_TARGET:-www.cloudflare.com:443}" \
REALITY_XHTTP_SNI="${REALITY_XHTTP_SNI:-www.apple.com}" REALITY_XHTTP_TARGET="${REALITY_XHTTP_TARGET:-www.apple.com:443}" \
REALITY_RAW2_SNI="${REALITY_RAW2_SNI:-www.bing.com}" REALITY_RAW2_TARGET="${REALITY_RAW2_TARGET:-www.bing.com:443}" \
XHTTP_PATH="${XHTTP_PATH:-/xhttp}"
python3 /opt/xray/scripts/generate.py
python3 - "$D/subscription.txt" <<'PY'
import sys
from pathlib import Path
lines=[x.strip() for x in Path(sys.argv[1]).read_text().splitlines() if x.strip()]
if len(lines)!=3:raise SystemExit(f"FATAL: expected exactly 3 nodes, got {len(lines)}")
for i,line in enumerate(lines,1): print(f"NODE_{i}={line}")
PY
xray run -test -config "$C"
xray run -config "$C" & XP=$!;GP="";trap 'kill "$XP" "$GP" 2>/dev/null||true;wait "$XP" 2>/dev/null||true;wait "$GP" 2>/dev/null||true' INT TERM EXIT
wait_port(){ h="$1";p="$2";label="$3";i=0;while :;do if python3 -c 'import socket,sys;s=socket.create_connection((sys.argv[1],int(sys.argv[2])),1);s.close()' "$h" "$p" 2>/dev/null;then echo "READY_CHECK=$label:$p";return 0;fi;if ! kill -0 "$XP" 2>/dev/null;then echo "FATAL: xray exited before $label:$p" >&2;exit 1;fi;i=$((i+1));[ "$i" -lt "${READY_TIMEOUT:-90}" ]||{ echo "FATAL: readiness timeout $label:$p" >&2;exit 1;};sleep 1;done;}
wait_port 127.0.0.1 10086 xhttp-http;wait_port 127.0.0.1 10087 raw-reality-vision-01;wait_port 127.0.0.1 10088 xhttp-reality;wait_port 127.0.0.1 10089 raw-reality-vision-02
python3 /opt/xray/scripts/gateway.py & GP=$!
wait_port 127.0.0.1 8080 protocol-router
printf '%s/sub/%s\n' "https://${PUBLIC_DOMAIN}" "$TOKEN">"$D/subscription_url.txt";chmod 600 "$D/subscription_url.txt"
echo "RELEASE=$BUILD_ID";echo "ARCHITECTURE=single-8080-sni-router";echo "SUBSCRIPTION_INVARIANT=3";echo "TARGET_PORT=8080";echo "TCP=$TCP_HOST:$TCP_PORT -> 8080";echo "ROUTES=RAW1->10087,XHTTP-REALITY->10088,RAW2->10089";echo "NODES=3"
while kill -0 "$XP" 2>/dev/null&&kill -0 "$GP" 2>/dev/null;do sleep 5;done;echo "FATAL: supervised process exited" >&2;exit 1
