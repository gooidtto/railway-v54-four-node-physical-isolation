from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text()


def test_single_8080_optional_four_node_topology():
    start = read("scripts/start.sh")
    generate = read("scripts/generate.py")
    gateway = read("scripts/gateway.py")
    docker = read("Dockerfile")
    railway = read("railway.toml")

    assert "GATEWAY_PORT=8080" in start
    assert "TARGET_PORT=8080" in start
    assert "APP_PORT = 8080" in generate or "APP_PORT=8080" in generate
    assert "10086" in generate and "10087" in generate and "10088" in generate
    assert '"count": NODE_COUNT' in generate
    assert "NODES=$EXPECTED" in start
    assert "8081" not in docker and "8082" not in docker and "8083" not in docker
    assert "EXPOSE 8080" in docker
    assert 'healthcheckPath = "/ready"' in railway
    assert "healthcheckTimeout = 300" in railway


def test_gateway_has_deep_readiness_and_limits():
    gateway = read("scripts/gateway.py")
    assert "asyncio.start_server(handle, \"0.0.0.0\", PORT" in gateway
    assert "GATEWAY_MAX_CONNECTIONS" in gateway
    assert "GATEWAY_UPSTREAM_TIMEOUT" in gateway
    assert "GATEWAY_IDLE_TIMEOUT" in gateway
    assert "def readiness():" in gateway
    assert 'path in ("/health", "/ready")' in gateway
    assert "local_port_ready(10086)" in gateway
    assert "local_port_ready(10087)" in gateway
    assert "local_port_ready(10088)" in gateway
    assert "cloudflare_ready()" in gateway
    assert "asyncio.wait_for(asyncio.open_connection" in gateway


def test_subscription_invariant_supports_three_or_four_nodes():
    generate = read("scripts/generate.py")
    start = read("scripts/start.sh")
    gateway = read("scripts/gateway.py")
    assert "NODE_COUNT = len(lines)" in generate
    assert "NODE_COUNT not in (3, 4)" in generate
    assert "expected not in (3, 4)" in gateway
    assert "case \"$EXPECTED\" in" in start
    assert "3|4)" in start


def test_healthcheck_uses_ready_endpoint():
    docker = read("Dockerfile")
    railway = read("railway.toml")
    assert 'urlopen(\'http://127.0.0.1:8080/ready\'' in docker
    assert 'healthcheckPath = "/ready"' in railway


def test_gateway_tls_parser_handles_fragmented_clienthello():
    gateway = read("scripts/gateway.py")
    assert "def _tls_client_hello(buf):" in gateway
    assert "reassembles handshake bytes across TLS records" in gateway
    assert "def _parse_client_hello_sni(handshake):" in gateway
    assert "strip().lower().rstrip(\".\")" in gateway
    assert 'ROUTE_REJECT tls_sni=%s' in gateway
    assert 'ROUTE_REJECT unknown_protocol=0x%s' in gateway
    assert 'len(b) >= 5 + struct.unpack("!H", b[3:5])[0]' not in gateway
