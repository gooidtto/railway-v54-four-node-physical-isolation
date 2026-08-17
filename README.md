# Railway VLESS — Fixed 8-Node Baseline

This is the restored, fixed baseline: **one HTTPS XHTTP node + seven REALITY Vision SNI nodes**. It deliberately avoids the multi-TCP-Proxy topology and Railway API discovery used by the failed v54 experiment.

## Topology

```text
                         Railway
                            │
              ┌─────────────┴─────────────┐
              │                           │
      Generate Domain                 TCP Proxy
      *.up.railway.app               *.proxy.rlwy.net:*
              │                           │
              │ HTTPS / L7               │ raw TCP
              ▼                           ▼
            :8080                       :8080
         HTTP/TLS gateway          same gateway listener
              │                           │
              ▼                           │ TLS ClientHello
        127.0.0.1:10086                  │
        VLESS + XHTTP                    ▼
        security=none              127.0.0.1:10087
        (Railway terminates TLS)   VLESS + TCP + REALITY
                                         │
                                  7 fixed serverNames
```

The gateway has exactly two protocol paths:

```text
HTTP request / HTTP/2 → 10086 → VLESS XHTTP
TLS ClientHello       → 10087 → VLESS REALITY + Vision
```

No fallback chain and no second proxy core are used.

## Railway Networking

Create only:

1. Generate Domain → target/application port `8080`
2. **One** TCP Proxy → target/application port `8080`

Railway supplies the TCP Proxy's public domain and external port through:

```text
RAILWAY_TCP_PROXY_DOMAIN
RAILWAY_TCP_PROXY_PORT
RAILWAY_TCP_APPLICATION_PORT=8080
```

The startup script reads these values directly. The public endpoint is not copied from persistent state.

## Fixed eight nodes

The subscription contains exactly eight VLESS links:

1. `VLESS XHTTP TLS` — `https://<railway-domain>:443`
2. `VLESS REALITY Vision 01` — SNI `www.cloudflare.com`
3. `VLESS REALITY Vision 02` — SNI `www.bing.com`
4. `VLESS REALITY Vision 03` — SNI `www.canva.com`
5. `VLESS REALITY Vision 04` — SNI `www.notion.so`
6. `VLESS REALITY Vision 05` — SNI `store.epicgames.com`
7. `VLESS REALITY Vision 06` — SNI `www.gog.com`
8. `VLESS REALITY Vision 07` — SNI `www.gamespot.com`

The seven SNI values are fixed in `config/reality-sni-candidates.txt`; startup fails if the list is not exactly seven entries.

The seven REALITY nodes all use the same Railway TCP Proxy endpoint and the same REALITY keypair/short ID. Their only intentional profile difference is the SNI/serverName.

## Persistent state

The volume persists only cryptographic identity and subscription authentication state:

```text
UUID
REALITY private/public key
short ID
subscription token
```

The Railway TCP Proxy hostname and external port are runtime values and are never restored from the volume.

## Expected startup

```text
BUILD=fixed-8-node-baseline
TCP_PROXY=<current-railway-tcp-domain>:<current-port> -> gateway:8080
REALITY=127.0.0.1:10087 SNI_COUNT=7
XHTTP_TLS=<railway-domain>:443 -> 127.0.0.1:10086
NODES=8 (1 HTTPS XHTTP + 7 REALITY Vision SNI)
READY: build=fixed-8-node-baseline gateway=8080 xray_reality=10087 xray_xhttp=10086 ...
```

## Subscription

After deployment:

```text
https://<your-railway-domain>/sub/<generated-token>
```

The response is Base64 encoded and contains exactly eight VLESS links.

## Xray

One Xray core is used. The private listeners are:

```text
10086 → VLESS + XHTTP (Railway HTTPS already terminated)
10087 → VLESS + TCP + REALITY + Vision (seven SNI profiles)
```

The next three experimental protocols are intentionally **not included** in this baseline. They should only be tested after this eight-node baseline is confirmed stable.
