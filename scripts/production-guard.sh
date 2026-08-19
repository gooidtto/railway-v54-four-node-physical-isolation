#!/bin/sh
set -eu

# Runtime-discovered deployment. No project/release/node names are hard-coded.
# Railway networking is authoritative for the current deployment.
PUBLIC_DOMAIN="${RAILWAY_PUBLIC_DOMAIN:-}"
TCP_HOST="${RAILWAY_TCP_PROXY_DOMAIN:-}"
TCP_PORT="${RAILWAY_TCP_PROXY_PORT:-}"
[ -n "$PUBLIC_DOMAIN" ] || { echo "FATAL: RAILWAY_PUBLIC_DOMAIN unavailable" >&2; exit 1; }
[ -n "$TCP_HOST" ] && [ -n "$TCP_PORT" ] || { echo "FATAL: Railway TCP Proxy unavailable" >&2; exit 1; }
case "$TCP_PORT" in ''|*[!0-9]*) echo "FATAL: RAILWAY_TCP_PROXY_PORT must be numeric" >&2; exit 1;; esac
[ "$TCP_PORT" -ge 1 ] && [ "$TCP_PORT" -le 65535 ] || { echo "FATAL: Railway TCP Proxy port out of range" >&2; exit 1; }

# Cloudflare is capability-based: complete configuration enables Node 4;
# absent configuration leaves the base Railway topology intact. A partial
# configuration is an error rather than a silent, ambiguous deployment.
cf_count=0
for name in CLOUDFLARE_TUNNEL_TOKEN CLOUDFLARE_TUNNEL_ID CLOUDFLARE_PUBLIC_HOSTNAME CLOUDFLARE_ORIGIN_SERVICE WS_PORT WS_PATH; do
  eval "value=\${$name:-}"
  [ -n "$value" ] && cf_count=$((cf_count + 1))
done
if [ "$cf_count" -ne 0 ] && [ "$cf_count" -ne 6 ]; then
  echo "FATAL: incomplete Cloudflare configuration ($cf_count/6 variables present)" >&2
  exit 1
fi
if [ "$cf_count" -eq 6 ]; then
  case "$WS_PORT" in ''|*[!0-9]*) echo "FATAL: WS_PORT must be numeric" >&2; exit 1;; esac
  [ "$WS_PORT" -ge 1 ] && [ "$WS_PORT" -le 65535 ] || { echo "FATAL: WS_PORT out of range" >&2; exit 1; }
  [ "$WS_PORT" != "8080" ] && [ "$WS_PORT" != "10086" ] && [ "$WS_PORT" != "10087" ] && [ "$WS_PORT" != "10088" ] || { echo "FATAL: WS_PORT conflicts with an internal port" >&2; exit 1; }
  case "$WS_PATH" in /*) ;; *) echo "FATAL: WS_PATH must start with /" >&2; exit 1;; esac
fi

export NODE_MODE="${NODE_MODE:-auto}"
export EXPECTED_NODES="${EXPECTED_NODES:-auto}"

python3 /opt/xray/scripts/runtime-manifest.py

echo "PRODUCTION_GUARD=PASS"
echo "NODE_MODE=$NODE_MODE"
echo "EXPECTED_NODES=$EXPECTED_NODES"
echo "RAILWAY_PUBLIC_DOMAIN=$PUBLIC_DOMAIN"
echo "RAILWAY_TCP_PROXY=$TCP_HOST:$TCP_PORT"
[ "$cf_count" -eq 6 ] && echo "CLOUDFLARE_CAPABILITY=enabled" || echo "CLOUDFLARE_CAPABILITY=disabled"
exec /opt/xray/scripts/start.sh
