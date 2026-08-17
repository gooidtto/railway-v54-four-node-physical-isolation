from pathlib import Path


def test_fixed_8_node_baseline():
    start = Path("scripts/start.sh").read_text()
    generate = Path("scripts/generate.py").read_text()
    gateway = Path("scripts/gateway.py").read_text()
    sni = Path("config/reality-sni-candidates.txt").read_text().splitlines()

    assert len(sni) == 7
    assert 'RAILWAY_TCP_PROXY_DOMAIN' in start
    assert 'RAILWAY_TCP_PROXY_PORT' in start
    assert 'RAILWAY_TCP_APPLICATION_PORT' in start
    assert '10086' in start and '10087' in start
    assert '8080' in start
    assert 'REALITY_PORT = 10087' in generate
    assert 'HTTP_PORT = 10086' in generate
    assert 'NODES=8' in start
    assert 'tls-reality' in gateway
    assert 'http-xhttp' in gateway


def test_old_dynamic_multi_proxy_logic_is_removed():
    start = Path("scripts/start.sh").read_text()
    assert 'XHTTP_REALITY_PUBLIC_HOST' not in start
    assert 'GRPC_REALITY_PUBLIC_HOST' not in start
    assert 'RAILWAY_PROJECT_TOKEN' not in start
    assert 'discover_tcp_proxies.py' not in start
