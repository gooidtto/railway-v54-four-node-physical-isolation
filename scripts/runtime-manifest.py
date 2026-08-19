#!/usr/bin/env python3
"""Generate deployment metadata from the current runtime environment.

This file intentionally contains no project-specific release name or node
names. The current Railway deployment supplies the endpoint data; the
configuration supplies only protocol capabilities.
"""
import hashlib
import json
import os
import re
from pathlib import Path

D = Path(os.environ.get("DATA_DIR", "/data"))
D.mkdir(parents=True, exist_ok=True)

public = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
tcp_host = os.environ.get("RAILWAY_TCP_PROXY_DOMAIN", "").strip()
tcp_port = os.environ.get("RAILWAY_TCP_PROXY_PORT", "").strip()
cf_names = (
    "CLOUDFLARE_TUNNEL_TOKEN", "CLOUDFLARE_TUNNEL_ID",
    "CLOUDFLARE_PUBLIC_HOSTNAME", "CLOUDFLARE_ORIGIN_SERVICE",
    "WS_PORT", "WS_PATH",
)
cf = {k: os.environ.get(k, "").strip() for k in cf_names}
cf_count = sum(bool(v) for v in cf.values())
cf_enabled = cf_count == len(cf_names)

nodes = []
if public:
    nodes.append({
        "id": "node-01",
        "name": os.environ.get("NODE_01_NAME", "Node 01").strip() or "Node 01",
        "transport": "xhttp", "security": "tls",
        "endpoint_source": "railway_public_domain",
        "endpoint": f"{public}:443",
    })
if tcp_host and tcp_port:
    raw_sni = os.environ.get("REALITY_RAW_SNI", "www.cloudflare.com").strip()
    xhttp_sni = os.environ.get("REALITY_XHTTP_SNI", "www.apple.com").strip()
    nodes.append({
        "id": "node-02",
        "name": os.environ.get("NODE_02_NAME", "Node 02").strip() or "Node 02",
        "transport": "tcp", "security": "reality", "flow": "xtls-rprx-vision",
        "sni": raw_sni, "endpoint_source": "railway_tcp_proxy",
        "endpoint": f"{tcp_host}:{tcp_port}",
    })
    nodes.append({
        "id": "node-03",
        "name": os.environ.get("NODE_03_NAME", "Node 03").strip() or "Node 03",
        "transport": "xhttp", "security": "reality",
        "sni": xhttp_sni, "endpoint_source": "railway_tcp_proxy",
        "endpoint": f"{tcp_host}:{tcp_port}",
    })
if cf_enabled:
    nodes.append({
        "id": "node-04",
        "name": os.environ.get("NODE_04_NAME", "Node 04").strip() or "Node 04",
        "transport": "ws", "security": "cloudflare",
        "endpoint_source": "cloudflare_tunnel",
        "endpoint": f"{cf['CLOUDFLARE_PUBLIC_HOSTNAME']}:443",
        "path": cf["WS_PATH"],
    })

policy = {
    "node_count": len(nodes),
    "cloudflare_configured": cf_enabled,
    "networking_source": "current-deployment-environment",
    "names_source": "runtime-config-or-default-node-id",
    "endpoints_source": "current-railway-environment",
}
manifest = {
    "schema": 1,
    "kind": "runtime-deployment-manifest",
    "project": {
        "name": os.environ.get("PROJECT_NAME", "").strip() or None,
        "release": os.environ.get("RELEASE_NAME", "").strip() or None,
    },
    "policy": policy,
    "nodes": nodes,
    "capabilities": {
        "single_gateway": True,
        "sni_routing": True,
        "fragmented_clienthello": True,
        "dynamic_railway_networking": True,
        "subscription_generation": True,
        "uuid_invariant": True,
        "cloudflare_tunnel": cf_enabled,
    },
}
manifest["fingerprint"] = hashlib.sha256(
    json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
(D / "runtime-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(f"RUNTIME_NODE_COUNT={len(nodes)}")
print(f"RUNTIME_CLOUDFLARE={'enabled' if cf_enabled else 'disabled'}")
print(f"RUNTIME_MANIFEST={D / 'runtime-manifest.json'}")
print(f"RUNTIME_MANIFEST_FINGERPRINT={manifest['fingerprint']}")
for n in nodes:
    print(f"NODE_DISCOVERED={n['id']} name={n['name']} transport={n['transport']} security={n['security']} endpoint={n['endpoint']}")
