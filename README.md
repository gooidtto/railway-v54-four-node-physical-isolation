# Railway VLESS Four-Node — Physical Isolation

This project implements the four-node topology without sharing a TCP listener between incompatible transports.

## Topology

```text
Railway Generate Domain (*.up.railway.app)
        │
        │ HTTPS / L7
        ▼
      :8080
   HTTP Gateway
        │
        ▼
   127.0.0.1:10086
   VLESS + XHTTP + TLS
   (TLS terminates at Railway)

TCP Proxy → application port 8081
        │
        ▼
   VLESS + RAW/TCP + REALITY + Vision

TCP Proxy → application port 8082
        │
        ▼
   VLESS + XHTTP + REALITY

TCP Proxy → application port 8083
        │
        ▼
   VLESS + gRPC + REALITY
```

### Physical isolation

A single Railway TCP Proxy cannot physically deliver three incompatible REALITY transports to three different Xray listeners. This version intentionally uses three TCP Proxy endpoints, each mapped to a dedicated internal port.

## Railway Networking

Create:

1. Generate Domain → target/application port `8080`
2. TCP Proxy → target/application port `8081`
3. TCP Proxy → target/application port `8082`
4. TCP Proxy → target/application port `8083`

Railway generates the public domain and external proxy port for each TCP Proxy. The project can now discover all three endpoints dynamically from Railway's Public GraphQL API by matching `applicationPort`.

## Dynamic TCP proxy discovery

Set a Railway **Project Token** as a sealed service variable:

```text
RAILWAY_PROJECT_TOKEN=<your Railway project token>
```

The container already receives these Railway system variables:

```text
RAILWAY_SERVICE_ID
RAILWAY_ENVIRONMENT_ID
RAILWAY_TCP_PROXY_DOMAIN
RAILWAY_TCP_PROXY_PORT
RAILWAY_TCP_APPLICATION_PORT
```

At startup, when `RAILWAY_PROJECT_TOKEN` (or `RAILWAY_API_TOKEN`) is present, `scripts/discover_tcp_proxies.py` queries:

```text
https://backboard.railway.com/graphql/v2
```

using Railway's documented `tcpProxies(serviceId, environmentId)` query and selects proxies by their target application port:

```text
8081 → Vision
8082 → XHTTP REALITY
8083 → gRPC REALITY
```

The discovered public host/port values are written only to a runtime temporary environment file and are **never persisted as subscription state**. This means a Railway-generated hostname or external port can change without requiring a code change or restoration of an old endpoint.

For project tokens, the API uses Railway's `Project-Access-Token` header. Account/workspace tokens may instead be supplied as `RAILWAY_API_TOKEN` and use the Bearer authorization header.

### Fallback mode

If no Railway API token is configured, the project keeps a compatibility fallback:

- Vision uses `VISION_PUBLIC_*` or Railway's automatic `RAILWAY_TCP_PROXY_*` variables.
- XHTTP REALITY uses `XHTTP_REALITY_PUBLIC_*`.
- gRPC REALITY uses `GRPC_REALITY_PUBLIC_*`.

For fully automatic endpoint tracking, configure `RAILWAY_PROJECT_TOKEN`.

## Four nodes

The container generates exactly four VLESS links:

- VLESS + RAW/TCP + REALITY + Vision
- VLESS + XHTTP + REALITY
- VLESS + XHTTP + TLS
- VLESS + gRPC + REALITY

The persistent volume keeps UUID, REALITY keypair, short ID, and subscription token. Public TCP endpoint host/port values are deliberately **not** restored from persistent state.

## Stale-state protection

The build rejects the previously observed stale endpoint:

```text
altaria.proxy.rlwy.net:32227
```

A deployment is not considered correct unless its logs contain:

```text
BUILD=v54-four-node-physical-isolation
TOPOLOGY=8080/http-gateway 8081/vision 8082/xhttp-reality 8083/grpc-reality 10086/xhttp-tls
READY: build=v54-four-node-physical-isolation http=8080 vision=8081 xhttp-reality=8082 grpc-reality=8083 xhttp-tls=10086
```

## Subscription

After deployment:

```text
https://<your-railway-domain>/sub/<generated-token>
```

The subscription response is Base64 encoded and contains four VLESS links.

## Xray

The project uses one Xray core. No second proxy core is required.
