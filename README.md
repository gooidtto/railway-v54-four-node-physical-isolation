# Railway VLESS — Single 8080 Router v1

The supported deployment has exactly four VLESS nodes and one Railway application target port: **8080**.

## Runtime topology

```text
                         Railway
                            |
              +-------------+-------------+
              |                           |
       Generate Domain                TCP Proxies
       *.up.railway.app              *.proxy.rlwy.net
              |                           |
             :443                    :random ports
              |                           |
             8080                       8080
              |                           |
              +-------------+-------------+
                            |
                           Router
                            |
             +--------------+--------------+
             |              |              |
          HTTP/TLS        TCP/TLS        TCP/TLS
             |              |              |
           XHTTP          SNI route       SNI route
             |              |              |
          10086         +---+---+       +---+---+
                        |       |       |       |
                     10087   10088   10089
                     REALITY REALITY WS TLS
                     Vision  gRPC
```

### Four nodes

1. Railway Public Domain `:443` — VLESS XHTTP + TLS at the Railway edge; plaintext HTTP reaches the single `8080` router and is forwarded to Xray `10086`.
2. TCP Proxy #1 — VLESS RAW/TCP + REALITY + Vision; SNI `www.cloudflare.com` routes to Xray `10087`.
3. TCP Proxy #2 — VLESS gRPC + REALITY; SNI `www.apple.com` routes to Xray `10088`.
4. TCP Proxy #3 — VLESS WS + TLS; SNI is the TCP Proxy #3 hostname and routes to Xray `10089`.

All three TCP Proxies target **8080**. There are no Railway targets on 8081/8082/8083.

## Single-entry routing

The Python router listens only on `0.0.0.0:8080`:

```text
HTTP request              -> 10086 XHTTP
TLS SNI www.cloudflare.com -> 10087 REALITY Vision
TLS SNI www.apple.com      -> 10088 gRPC REALITY
TLS SNI <WS host>          -> 10089 WS TLS
```

Unknown TLS SNI is rejected. `/ready`, `/sub/<token>`, `/`, and `/index.html` remain HTTP routes on the same 8080 listener.

## Xray

Xray remains the only proxy core. Its four private inbounds listen on `127.0.0.1:10086-10089`; these are container-internal ports, not Railway application ports.

## Deployment invariant

```text
RELEASE=fixed-single-8080-router-v1
ARCHITECTURE=single-8080-protocol-router
TARGET_PORT=8080
SUBSCRIPTION_INVARIANT=4
NODES=4
```

The old `8081/8082/8083` Railway application-port architecture, physical four-entry Gateway, 7-node mode, 8-node mode, and `3+2+2` distribution are removed from the supported topology.
