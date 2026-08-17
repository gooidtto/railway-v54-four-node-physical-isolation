# Railway VLESS — Fixed 4-Node Physical Isolation v4

This is the only supported release on `main`. The deployment must expose exactly four VLESS nodes.

## Runtime topology

```text
Railway Public Domain :443
        |
        v
Gateway :8080 -> Xray 127.0.0.1:10086
VLESS + XHTTP + TLS

TCP Proxy #1 -> 8081 -> 10087
VLESS + RAW/TCP + REALITY + Vision

TCP Proxy #2 -> 8082 -> 10088
VLESS + gRPC + REALITY

TCP Proxy #3 -> 8083 -> 10089
VLESS + WS + TLS
```

## Exactly four subscription nodes

1. Railway Public Domain `:443` — VLESS XHTTP TLS
2. TCP Proxy #1 — VLESS RAW/TCP REALITY Vision
3. TCP Proxy #2 — VLESS gRPC REALITY
4. TCP Proxy #3 — VLESS WS TLS

There is no 8-node mode, no 7-SNI distribution, and no `3+2+2` REALITY node generation in this release.

## Runtime-only Railway networking

The public domain and all TCP proxy domains/ports are read from Railway runtime variables. No old public endpoint is used as a fallback.

Required variables:

```text
RAILWAY_PUBLIC_DOMAIN
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

The application targets must be exactly:

```text
8080 -> 10086
8081 -> 10087
8082 -> 10088
8083 -> 10089
```

If any required Railway runtime value is missing or a target is incorrect, startup fails instead of silently deploying an old/default topology.

## Deployment identity

```text
RELEASE=fixed-4-node-physical-isolation-v4
SUBSCRIPTION_INVARIANT=4
NODES=4
```

The old 8-node release manifest has been removed from the authoritative release definition. The persistent volume may retain credentials and keys, but generated node lists, manifests, state, and short-ID lists are recreated for the current four-node release.
