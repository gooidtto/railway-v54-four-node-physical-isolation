# Railway Xray Gateway

A single-service Railway deployment for an Xray gateway with persistent runtime state and an optional Cloudflare WebSocket node.

## Architecture

- One Railway service, internal port `8080`
- One persistent Volume mounted at `/data`
- One Railway Public Domain + TCP Proxy
- One replica when using the Volume
- Base topology: **3 nodes**
- Optional Cloudflare Tunnel: **4th node**
- Health endpoint: `GET /ready`

Railway-provided networking values are read at runtime; domains and proxy ports are never hard-coded.

## Deploy

1. Deploy the repository from the `main` branch.
2. Create a Railway Volume at `/data`.
3. Create a Public Domain and a TCP Proxy targeting internal port `8080`.
4. Redeploy after networking is available.

The base deployment generates **3 nodes**. Persistent state under `/data` keeps the generated identity and subscription state stable across redeployments.

### Optional fourth node

Set the complete Cloudflare configuration:

```text
CLOUDFLARE_TUNNEL_TOKEN
CLOUDFLARE_TUNNEL_ID
CLOUDFLARE_PUBLIC_HOSTNAME
CLOUDFLARE_ORIGIN_SERVICE
WS_PORT
WS_PATH
```

When all values are valid, the deployment exposes **4 nodes**. Partial configuration does not create a fourth node.

## Runtime behavior

Each deployment reads the current Railway networking values and regenerates runtime output from them. Previous `/data` state is used for continuity and diagnostics, not as an override for current Railway networking.

The supervisor monitors the gateway, Xray, and optional Cloudflare tunnel processes. Docker and Railway health checks use `/ready`.

## Repository layout

```text
.
├── config/          Xray configuration templates
├── scripts/         Runtime, gateway, generation, and validation logic
├── site/            Public dashboard
├── Dockerfile
├── railway.toml
└── README.md
```

## Security

Never commit tunnel tokens, private keys, generated credentials, subscription URLs, or deployment-specific domains. Keep secrets in Railway environment variables and generated state in the persistent Volume.

## License

Add the repository license appropriate to your use case.
