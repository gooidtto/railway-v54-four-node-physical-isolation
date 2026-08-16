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

Railway TCP Proxy #1
        │
        ▼
   127.0.0.1:8081
   VLESS + RAW/TCP + REALITY + Vision

Railway TCP Proxy #2
        │
        ▼
   127.0.0.1:8082
   VLESS + XHTTP + REALITY

Railway TCP Proxy #3
        │
        ▼
   127.0.0.1:8083
   VLESS + gRPC + REALITY
```

### Important

A single Railway TCP Proxy cannot physically deliver three incompatible REALITY transports to three different Xray listeners. Therefore this version intentionally uses **three TCP Proxy endpoints**, each mapped to a dedicated internal port.

## Railway Networking

Create:

1. Generate Domain → target/application port `8080`
2. TCP Proxy #1 → target/application port `8081`
3. TCP Proxy #2 → target/application port `8082`
4. TCP Proxy #3 → target/application port `8083`

Set these runtime variables using the actual current Railway TCP Proxy endpoints:

```text
VISION_PUBLIC_HOST
VISION_PUBLIC_PORT

XHTTP_REALITY_PUBLIC_HOST
XHTTP_REALITY_PUBLIC_PORT

GRPC_REALITY_PUBLIC_HOST
GRPC_REALITY_PUBLIC_PORT
```

Do not reuse an old TCP Proxy endpoint.

The HTTP domain can be supplied automatically through `RAILWAY_PUBLIC_DOMAIN`; `PUBLIC_DOMAIN` may also be set explicitly.

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
