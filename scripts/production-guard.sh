#!/bin/sh
set -eu

MODE="${NODE_MODE:-4}"
if [ "$MODE" != "4" ]; then
  echo "FATAL: railway-production-v5 requires NODE_MODE=4" >&2
  exit 1
fi

required="CLOUDFLARE_TUNNEL_TOKEN CLOUDFLARE_TUNNEL_ID CLOUDFLARE_PUBLIC_HOSTNAME CLOUDFLARE_ORIGIN_SERVICE WS_PORT WS_PATH"
for name in $required; do
  eval "value=\${$name:-}"
  [ -n "$value" ] || { echo "FATAL: NODE_MODE=4 requires $name" >&2; exit 1; }
done

case "$WS_PORT" in
  ''|*[!0-9]*) echo "FATAL: WS_PORT must be numeric" >&2; exit 1 ;;
esac
[ "$WS_PORT" -ge 1 ] && [ "$WS_PORT" -le 65535 ] || { echo "FATAL: WS_PORT out of range" >&2; exit 1; }
[ "$WS_PORT" != "8080" ] && [ "$WS_PORT" != "10086" ] && [ "$WS_PORT" != "10087" ] && [ "$WS_PORT" != "10088" ] || { echo "FATAL: WS_PORT conflicts with an internal port" >&2; exit 1; }
case "$WS_PATH" in
  /*) ;;
  *) echo "FATAL: WS_PATH must start with /" >&2; exit 1 ;;
esac

echo "PRODUCTION_GUARD=PASS"
echo "NODE_MODE=4"
echo "EXPECTED_NODES=4"
exec /opt/xray/scripts/start.sh
