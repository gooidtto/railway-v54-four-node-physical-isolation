# Railway VLESS — Fixed 8-Node / Three-TCP Baseline

This version matches the current Railway Networking layout instead of silently using only the first TCP Proxy.

## Current Railway Networking

```text
Generate Domain
<railway-domain>.up.railway.app -> 8080

TCP Proxy #1
reseau.proxy.rlwy.net:23337 -> 8081

TCP Proxy #2
interchange.proxy.rlwy.net:23389 -> 8082

TCP Proxy #3
altaria.proxy.rlwy.net:17903 -> 8083
```

The three TCP proxy public endpoints are runtime-configurable through:

```text
RAILWAY_TCP_PROXY_DOMAIN
RAILWAY_TCP_PROXY_PORT
RAILWAY_TCP_APPLICATION_PORT

RAILWAY_TCP_PROXY_2_DOMAIN
RAILWAY_TCP_PROXY_2_PORT
RAILWAY_TCP_PROXY_2_APPLICATION_PORT

RAILWAY_TCP_PROXY_3_DOMAIN
RAILWAY_TCP_PROXY_3_PORT
RAILWAY_TCP_PROXY_3_APPLICATION_PORT
```

The defaults are the current Networking values above.

## Data flow

```text
Generate Domain :443
        |
        v
Gateway :8080
        |
        v
Xray 127.0.0.1:10086
VLESS + XHTTP (security=none)

TCP #1 :23337 -> 8081 ----+
TCP #2 :23389 -> 8082 ----+--> Gateway --> Xray 127.0.0.1:10087
TCP #3 :17903 -> 8083 ----+    VLESS + TCP + REALITY + Vision
```

All three TCP targets are separate Gateway listeners, but they deliberately feed the same REALITY listener. There is no fallback chain and no second proxy core.

## Fixed eight nodes

The subscription contains exactly eight VLESS links:

1. `VLESS XHTTP TLS` — Railway Domain `:443`
2-4. `VLESS REALITY Vision` — TCP Proxy #1 (`reseau:23337 -> 8081`), SNI 01-03
5-6. `VLESS REALITY Vision` — TCP Proxy #2 (`interchange:23389 -> 8082`), SNI 04-05
7-8. `VLESS REALITY Vision` — TCP Proxy #3 (`altaria:17903 -> 8083`), SNI 06-07

The seven fixed SNI values remain:

```text
www.cloudflare.com
www.bing.com
www.canva.com
www.notion.so
store.epicgames.com
www.gog.com
www.gamespot.com
```

The server uses one Xray core with two private inbounds:

```text
10086 -> VLESS + XHTTP
10087 -> VLESS + TCP + REALITY + Vision (7 SNI)
```

The three Railway TCP proxies are physical/public entry separation only; they do not create additional Xray inbounds.

## Important

Do not add a fourth TCP Proxy. Do not add 8084. Do not enable WS in this baseline.

If Railway regenerates any TCP external port, set the corresponding `RAILWAY_TCP_PROXY_*` variables to the new values. The subscription is generated from those runtime values, not from persisted old state.
