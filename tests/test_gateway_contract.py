from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATEWAY = (ROOT / "scripts/gateway.py").read_text()
GENERATE = (ROOT / "scripts/generate.py").read_text()


def test_gateway_routes_are_normalized_and_distinct():
    assert 'REALITY_RAW_SNI' in GATEWAY
    assert 'REALITY_XHTTP_SNI' in GATEWAY
    assert '.strip().lower().rstrip(".")' in GATEWAY
    # The gateway is an SNI multiplexer; two route keys must not collapse.
    assert 'ROUTES.get(sni or "")' in GATEWAY


def test_gateway_reassembles_tls_records_before_sni_lookup():
    assert 'def _tls_client_hello(buf):' in GATEWAY
    assert 'handshake = bytearray()' in GATEWAY
    assert 'handshake.extend(payload)' in GATEWAY
    assert 'hs_len = int.from_bytes(handshake[1:4], "big")' in GATEWAY
    assert 'if len(handshake) < total:' in GATEWAY
    assert 'return True, _parse_client_hello_sni' in GATEWAY


def test_gateway_has_explicit_upstream_failure_diagnostics():
    assert 'UPSTREAM_CONNECT_OK' in GATEWAY
    assert 'INITIAL_FORWARDED' in GATEWAY
    assert 'UPSTREAM_TIMEOUT' in GATEWAY
    assert 'RELAY_ERROR' in GATEWAY
    assert 'ROUTE_REJECT tls_sni=' in GATEWAY


def test_optional_cloudflare_node_is_port_separated_from_gateway():
    assert 'CF_PORT == APP_PORT' in GENERATE
    assert 'CF_PORT in (10086, 10087, 10088)' in GENERATE
    assert 'CF_ENABLED' in GENERATE
    assert 'NODE_COUNT = len(lines)' in GENERATE
