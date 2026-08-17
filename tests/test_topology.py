from pathlib import Path


def test_fixed_8_node_stable_reality_baseline():
    start = Path("scripts/start.sh").read_text()
    generate = Path("scripts/generate.py").read_text()
    gateway = Path("scripts/gateway.py").read_text()
    sni = Path("config/reality-sni-candidates.txt").read_text().splitlines()

    assert len(sni) == 1
    assert sni == ["www.cloudflare.com"]
    assert 'RAILWAY_TCP_PROXY_DOMAIN' in start
    assert 'RAILWAY_TCP_PROXY_2_DOMAIN' in start
    assert 'RAILWAY_TCP_PROXY_3_DOMAIN' in start
    assert 'RAILWAY_TCP_PROXY_PORT' in start
    assert 'RAILWAY_TCP_APPLICATION_PORT' in start
    assert '8080,8081,8082,8083' in start
    assert '10086' in start and '10087' in start
    assert 'REALITY_PORT = 10087' in generate
    assert 'HTTP_PORT = 10086' in generate
    assert 'NODE_COUNT = 7' in generate
    assert 'short_ids' in generate
    assert 'serverNames": [reality_sni]' in generate
    assert 'NODES=8' in start
    assert 'tls-reality' in gateway
    assert 'http-xhttp' in gateway


def test_three_proxy_distribution_is_fixed():
    generate = Path("scripts/generate.py").read_text()
    assert 'proxy = proxies[0] if i <= 3 else proxies[1] if i <= 5 else proxies[2]' in generate
    assert '"distribution": [3, 2, 2]' in generate


def test_old_dynamic_multi_proxy_logic_is_removed():
    start = Path("scripts/start.sh").read_text()
    assert 'XHTTP_REALITY_PUBLIC_HOST' not in start
    assert 'GRPC_REALITY_PUBLIC_HOST' not in start
    assert 'RAILWAY_PROJECT_TOKEN' not in start
    assert 'discover_tcp_proxies.py' not in start
