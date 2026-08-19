# Railway Production V5

This profile is deployment-generic. It does not hard-code a project name, release name, Railway hostname, TCP port, Cloudflare hostname, or node display name.

At startup the container discovers the current Railway deployment from:

- `RAILWAY_PUBLIC_DOMAIN`
- `RAILWAY_TCP_PROXY_DOMAIN`
- `RAILWAY_TCP_PROXY_PORT`

Node 4 is discovered from the presence of the complete Cloudflare capability set. The resulting node count, endpoint values and runtime node manifest are generated for that deployment.

The core data plane remains stable: single gateway, SNI routing, fragmented ClientHello handling, Xray inbound profiles, dynamic Railway networking and subscription invariants.

Optional display names can be supplied as `NODE_01_NAME` through `NODE_04_NAME`; these never affect routing or protocol behavior.
