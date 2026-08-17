#!/bin/sh
set -eu
umask 077
BUILD_ID="stable-optional-cloudflare-ws"
D="${RAILWAY_VOLUME_MOUNT_PATH:-${DATA_DIR:-/data}}";C="${XRAY_CONFIG:-${D}/config.json}"
mkdir -p "$D" "$(dirname "$C")"
write_secret(){ f="$1";v="$2";t="$f.tmp";printf '%s\n' "$v">"$t";chmod 600 "$t";mv -f "$t" "$f"; }
PUBLIC_DOMAIN="${RAILWAY_PUBLIC_DOMAIN:-}";[ -n "$PUBLIC_DOMAIN" ]||{ echo "FATAL: RAILWAY_PUBLIC_DOMAIN unavailable" >&2;exit 1; }
TCP_HOST="${RAILWAY_TCP_PROXY_DOMAIN:-}";TCP_PORT="${RAILWAY_TCP_PROXY_PORT:-}"
[ -n "$TCP_HOST" ]&&[ -n "$TCP_PORT" ]||{ echo "FATAL: one Railway TCP Proxy is required" >&2;exit 1; }
UUID_FILE="$D/uuid.txt";PRIV_FILE="$D/reality_private_key.txt";PUB_FILE="$D/reality_public_key.txt";TOKEN_FILE="$D/subscription_token.txt";CF_TOKEN_FILE="$D/cloudflare_tunnel_token.txt"
if [ -s "$UUID_FILE" ];then UUID=$(tr -d '[:space:]'<"$UUID_FILE");else UUID=$(xray uuid);write_secret "$UUID_FILE" "$UUID";fi
if [ -s "$PRIV_FILE" ]&&[ -s "$PUB_FILE" ];then PRIVATE_KEY=$(tr -d '[:space:]'<"$PRIV_FILE");PUBLIC_KEY=$(tr -d '[:space:]'<"$PUB_FILE");else OUT="$(xray x25519 2>&1)";PRIVATE_KEY=$(printf '%s\n' "$OUT"|awk -F': ' '/^PrivateKey/{print $2;exit}');PUBLIC_KEY=$(printf '%s\n' "$OUT"|awk -F': ' '/^Password/{print $2;exit}');[ -n "$PRIVATE_KEY" ]&&[ -n "$PUBLIC_KEY" ]||{ echo "FATAL: failed to generate REALITY keys" >&2;exit 1;};write_secret "$PRIV_FILE" "$PRIVATE_KEY";write_secret "$PUB_FILE" "$PUBLIC_KEY";fi
if [ -s "$TOKEN_FILE" ];then TOKEN=$(tr -d '[:space:]'<"$TOKEN_FILE");else TOKEN=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))');write_secret "$TOKEN_FILE" "$TOKEN";fi
export DATA_DIR="$D" XRAY_CONFIG="$C" UUID PRIVATE_KEY PUBLIC_KEY PUBLIC_DOMAIN RAILWAY_TCP_PROXY_DOMAIN="$TCP_HOST" RAILWAY_TCP_PROXY_PORT="$TCP_PORT" GATEWAY_PORT=8080 REALITY_RAW_SNI="${REALITY_RAW_SNI:-www.cloudflare.com}" REALITY_RAW_TARGET="${REALITY_RAW_TARGET:-www.cloudflare.com:443}" REALITY_FINGERPRINT="${REALITY_FINGERPRINT:-chrome}" REALITY_XHTTP_SNI="${REALITY_XHTTP_SNI:-www.apple.com}" REALITY_XHTTP_TARGET="${REALITY_XHTTP_TARGET:-www.apple.com:443}" XHTTP_PATH="${XHTTP_PATH:-/xhttp}"
export CLOUDFLARE_TUNNEL_TOKEN="${CLOUDFLARE_TUNNEL_TOKEN:-}" CLOUDFLARE_TUNNEL_ID="${CLOUDFLARE_TUNNEL_ID:-}" CLOUDFLARE_PUBLIC_HOSTNAME="${CLOUDFLARE_PUBLIC_HOSTNAME:-}" CLOUDFLARE_ORIGIN_SERVICE="${CLOUDFLARE_ORIGIN_SERVICE:-}" WS_PORT="${WS_PORT:-}" WS_PATH="${WS_PATH:-}"
python3 /opt/xray/scripts/generate.py
python3 - "$D/subscription.txt" "$D/manifest.json" <<'PY'
import json,sys
from pathlib import Path
lines=[x.strip() for x in Path(sys.argv[1]).read_text().splitlines() if x.strip()]
manifest=json.loads(Path(sys.argv[2]).read_text())
expected=4 if manifest.get('cloudflare_ws_enabled') else 3
if len(lines)!=expected:raise SystemExit(f"FATAL: expected {expected} nodes, got {len(lines)}")
if manifest.get('node_count')!=expected:raise SystemExit('FATAL: manifest/subscription node count mismatch')
for i,line in enumerate(lines,1):
    if not line.startswith('vless://'):raise SystemExit(f"FATAL: invalid subscription node {i}")
    print(f"NODE_{i}={line}")
print(f"SUBSCRIPTION_COUNT={len(lines)}")
PY
xray run -test -config "$C"
xray run -config "$C" & XP=$!;GP="";CFP="";trap 'kill "$XP" "$GP" "$CFP" 2>/dev/null||true;wait "$XP" 2>/dev/null||true;wait "$GP" 2>/dev/null||true;wait "$CFP" 2>/dev/null||true' INT TERM EXIT
wait_port(){ h="$1";p="$2";label="$3";i=0;while :;do if python3 -c 'import socket,sys;s=socket.create_connection((sys.argv[1],int(sys.argv[2])),1);s.close()' "$h" "$p" 2>/dev/null;then echo "READY_CHECK=$label:$p";return 0;fi;if ! kill -0 "$XP" 2>/dev/null;then echo "FATAL: xray exited before $label:$p" >&2;exit 1;fi;i=$((i+1));[ "$i" -lt "${READY_TIMEOUT:-90}" ]||{ echo "FATAL: readiness timeout $label:$p" >&2;exit 1;};sleep 1;done;}
wait_http_ready(){ url="$1";label="$2";i=0;while :;do if python3 - "$url" <<'PY'
import sys,urllib.request
try:urllib.request.urlopen(sys.argv[1],timeout=2).read();raise SystemExit(0)
except Exception:raise SystemExit(1)
PY
then echo "READY_CHECK=$label";return 0;fi;i=$((i+1));[ "$i" -lt "${CLOUDFLARE_READY_TIMEOUT:-45}" ]||{ echo "FATAL: readiness timeout $label" >&2;exit 1;};sleep 1;done;}
wait_port 127.0.0.1 10086 xhttp-http;wait_port 127.0.0.1 10087 raw-reality-vision;wait_port 127.0.0.1 10088 xhttp-reality
CF_ENABLED=$(python3 - "$D/manifest.json" <<'PY'
import json,sys
print('1' if json.load(open(sys.argv[1])).get('cloudflare_ws_enabled') else '0')
PY
)
if [ "$CF_ENABLED" = 1 ];then
  wait_port 127.0.0.1 "$WS_PORT" cloudflare-ws-origin
fi
python3 /opt/xray/scripts/gateway.py & GP=$!
wait_port 127.0.0.1 8080 protocol-router
if [ "$CF_ENABLED" = 1 ];then
  write_secret "$CF_TOKEN_FILE" "$CLOUDFLARE_TUNNEL_TOKEN"
  echo "CLOUDFLARE_WS=enabled hostname=${CLOUDFLARE_PUBLIC_HOSTNAME} origin=${CLOUDFLARE_ORIGIN_SERVICE}"
  cloudflared --no-autoupdate tunnel --metrics 127.0.0.1:2000 run --token-file "$CF_TOKEN_FILE" >"$D/cloudflared.log" 2>&1 & CFP=$!
  sleep 1
  kill -0 "$CFP" 2>/dev/null||{ echo "FATAL: cloudflared exited during startup" >&2;tail -n 80 "$D/cloudflared.log" >&2||true;exit 1;}
  wait_http_ready "http://127.0.0.1:2000/ready" cloudflared-tunnel
else
  echo "CLOUDFLARE_WS=disabled"
fi
printf '%s/sub/%s\n' "https://${PUBLIC_DOMAIN}" "$TOKEN">"$D/subscription_url.txt";chmod 600 "$D/subscription_url.txt"
COUNT=3;[ "$CF_ENABLED" = 1 ]&&COUNT=4
echo "RELEASE=$BUILD_ID";echo "ARCHITECTURE=single-8080-router-plus-optional-cloudflare-tunnel";echo "SUBSCRIPTION_INVARIANT=$COUNT";echo "TARGET_PORT=8080";echo "TCP=$TCP_HOST:$TCP_PORT -> 8080";echo "ROUTES=HTTP->10086,RAW-REALITY->10087,XHTTP-REALITY->10088";[ "$CF_ENABLED" = 1 ]&&echo "ROUTES=CLOUDFLARE-WS->$WS_PORT";echo "NODES=$COUNT"
while kill -0 "$XP" 2>/dev/null&&kill -0 "$GP" 2>/dev/null;do if [ "$CF_ENABLED" = 1 ]&&! kill -0 "$CFP" 2>/dev/null;then echo "FATAL: cloudflared exited" >&2;tail -n 80 "$D/cloudflared.log" >&2||true;exit 1;fi;sleep 5;done;echo "FATAL: supervised process exited" >&2;exit 1
