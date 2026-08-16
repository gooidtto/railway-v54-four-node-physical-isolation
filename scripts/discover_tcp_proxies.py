#!/usr/bin/env python3
"""Discover Railway TCP proxies by target application port.

If a Railway API token is supplied, query the Public GraphQL API and map
application ports 8081/8082/8083 to their current public domain/port.
No public endpoint is persisted as state.

Authentication:
- RAILWAY_PROJECT_TOKEN -> Project-Access-Token header
- RAILWAY_API_TOKEN     -> Authorization: Bearer header

The script prints shell assignments to stdout. Values are validated before
being emitted so start.sh can safely source the generated file.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

API_URL = "https://backboard.railway.com/graphql/v2"
SERVICE_ID = os.environ.get("RAILWAY_SERVICE_ID", "")
ENVIRONMENT_ID = os.environ.get("RAILWAY_ENVIRONMENT_ID", "")
TOKEN = os.environ.get("RAILWAY_PROJECT_TOKEN") or os.environ.get("RAILWAY_API_TOKEN")
USE_PROJECT_TOKEN = bool(os.environ.get("RAILWAY_PROJECT_TOKEN"))

HOST_RE = re.compile(r"^[A-Za-z0-9.-]+$")
TARGETS = {8081: "VISION", 8082: "XREAL", 8083: "GRPC"}


def fail(message):
    print(f"TCP_DISCOVERY_ERROR={message}", file=sys.stderr)
    raise SystemExit(2)


def validate_endpoint(name, host, port, application_port):
    if not host or not HOST_RE.fullmatch(host):
        fail(f"invalid {name} host")
    try:
        port = int(port)
        application_port = int(application_port)
    except (TypeError, ValueError):
        fail(f"invalid {name} port")
    if not 1 <= port <= 65535:
        fail(f"invalid {name} public port")
    if application_port not in TARGETS:
        fail(f"unsupported TCP application port {application_port}")
    if host == "altaria.proxy.rlwy.net" and port == 32227:
        fail("stale endpoint altaria.proxy.rlwy.net:32227")


def query():
    if not TOKEN:
        fail("no Railway API token configured")
    if not SERVICE_ID or not ENVIRONMENT_ID:
        fail("missing RAILWAY_SERVICE_ID/RAILWAY_ENVIRONMENT_ID")

    query_text = """
    query tcpProxies($serviceId: String!, $environmentId: String!) {
      tcpProxies(serviceId: $serviceId, environmentId: $environmentId) {
        id
        domain
        proxyPort
        applicationPort
      }
    }
    """
    payload = json.dumps({
        "query": query_text,
        "variables": {
            "serviceId": SERVICE_ID,
            "environmentId": ENVIRONMENT_ID,
        },
    }).encode()

    headers = {"Content-Type": "application/json"}
    if USE_PROJECT_TOKEN:
        headers["Project-Access-Token"] = TOKEN
    else:
        headers["Authorization"] = f"Bearer {TOKEN}"

    req = urllib.request.Request(API_URL, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            body = response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        fail(f"Railway API request failed: {exc}")

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        fail("Railway API returned invalid JSON")

    if data.get("errors"):
        message = "; ".join(str(x.get("message", "GraphQL error")) for x in data["errors"])
        fail(message)

    proxies = ((data.get("data") or {}).get("tcpProxies") or [])
    return proxies


def main():
    proxies = query()
    found = {}

    for proxy in proxies:
        try:
            app = int(proxy.get("applicationPort"))
            public_port = int(proxy.get("proxyPort"))
        except (TypeError, ValueError):
            continue
        domain = str(proxy.get("domain") or "").strip()
        if app not in TARGETS:
            continue
        if app in found:
            fail(f"multiple TCP proxies target application port {app}")
        validate_endpoint(TARGETS[app], domain, public_port, app)
        found[app] = (domain, public_port)

    missing = [str(port) for port in sorted(TARGETS) if port not in found]
    if missing:
        fail("missing TCP proxy for application port(s): " + ",".join(missing))

    print("TCP_DISCOVERY_SOURCE=railway-api")
    for app, name in TARGETS.items():
        host, port = found[app]
        if name == "VISION":
            print(f"VISION_PUBLIC_HOST={host}")
            print(f"VISION_PUBLIC_PORT={port}")
        elif name == "XREAL":
            print(f"XHTTP_REALITY_PUBLIC_HOST={host}")
            print(f"XHTTP_REALITY_PUBLIC_PORT={port}")
        else:
            print(f"GRPC_REALITY_PUBLIC_HOST={host}")
            print(f"GRPC_REALITY_PUBLIC_PORT={port}")
        print(f"TCP_PROXY_{app}={host}:{port}")


if __name__ == "__main__":
    main()
