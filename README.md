# Railway single-8080 deployment

This repository is designed for a fresh Railway account and supports a staged deployment flow.

## Deployment model

- One Railway service.
- One application port: `8080`.
- One persistent Volume mounted at `/data`.
- One generated Public Domain.
- One Railway TCP Proxy targeting internal port `8080`.
- One replica when a Volume is attached.
- Base topology: **3 nodes**.
- Optional Cloudflare Tunnel: **4th node**.

The service derives its Railway public domain, TCP proxy host/port, and volume path from Railway-provided environment variables. Do not hard-code account-specific Railway domains or proxy ports in the repository.

## Fresh-account deployment sequence

### Phase 1 — initial deployment

1. Connect this repository to a new Railway project.
2. Select the `main` branch.
3. Deploy once with the new service before manually creating Public Networking.
4. The first deployment may fail because `RAILWAY_PUBLIC_DOMAIN`, `RAILWAY_TCP_PROXY_DOMAIN`, and `RAILWAY_TCP_PROXY_PORT` are not available until networking is configured.

This failure is expected for the staged setup; it is not a configuration to bypass by hard-coding a domain or proxy port.

### Phase 2 — create Railway networking

In the service's **Public Networking** settings, manually create:

- **one Public Domain**
- **one TCP Proxy** whose target/internal port is **8080**

Then redeploy `main`.

With those two Railway networking resources present and no Cloudflare variables configured, the generator produces exactly **3 subscription nodes**.

Expected runtime markers:

```text
SUBSCRIPTION_INVARIANT=3
SUBSCRIPTION_COUNT=3
NODES=3
CLOUDFLARE_WS=disabled
RELEASE=stable-optional-cloudflare-ws-v4
```

### Phase 3 — optional fourth node

To enable the fourth node, add the complete Cloudflare variable set to the Railway service:

```text
CLOUDFLARE_TUNNEL_TOKEN
CLOUDFLARE_TUNNEL_ID
CLOUDFLARE_PUBLIC_HOSTNAME
CLOUDFLARE_ORIGIN_SERVICE
WS_PORT
WS_PATH
```

Then redeploy.

The generator recognizes the complete set and adds the Cloudflare WS node. The resulting topology is exactly **4 nodes**.

Expected runtime markers:

```text
SUBSCRIPTION_INVARIANT=4
SUBSCRIPTION_COUNT=4
NODES=4
CLOUDFLARE_WS=enabled
RELEASE=stable-optional-cloudflare-ws-v4
```

Partial or invalid Cloudflare configuration does not create a fourth node. It is reported through `CLOUDFLARE_VALIDATION` and the base topology remains the supported 3-node configuration when the optional variables are incomplete.

## Persistent state

Create a Railway Volume mounted at:

```text
/data
```

The service persists generated identity and runtime state there, including its UUID, REALITY keys, subscription token, short IDs, runtime manifest, and subscription output. A fresh deployment without the Volume can generate a new identity and invalidate existing client configurations.

Keep the service at one replica when using the Volume.

## Health and restart behavior

The application health endpoint is:

```text
GET /ready
```

Both the Docker healthcheck and `railway.toml` use this endpoint. The supervisor also watches the Xray, gateway, and optional Cloudflare tunnel processes.

## Build identity

The build identity is intentionally fixed to:

```text
SOURCE_BUILD=main-hardened-v4
BUILD_ID=stable-optional-cloudflare-ws-v4
```

Runtime state, manifest output, and startup logs must use the same `v4` identity.

## Public dashboard

`site/index.html` is a public travel-themed dashboard. It must not expose deployment domains, Railway identifiers, proxy details, node topology, or internal runtime information.
