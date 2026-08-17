from pathlib import Path


def test_single_8080_four_node_topology():
    start = Path("scripts/start.sh").read_text()
    generate = Path("scripts/generate.py").read_text()
    gateway = Path("scripts/gateway.py").read_text()
    docker = Path("Dockerfile").read_text()

    assert 'GATEWAY_PORT=8080' in start
    assert 'TCP targets must be 8081:8082:8083' not in start
    assert 'GATEWAY_PORTS=8080,8081,8082,8083' not in start
    assert 'TARGET_PORT=8080' in start
    assert 'NODE_COUNT=4' in generate
    assert 'APP_PORT=8080' in generate
    assert 'RAILWAY_TCP_PROXY_2_DOMAIN' in generate
    assert 'RAILWAY_TCP_PROXY_3_DOMAIN' in generate
    assert 'REALITY_RAW_SNI' in generate
    assert 'REALITY_GRPC_SNI' in generate
    assert 'NODES=4' in start
    assert 'SNI' in gateway
    assert 'tls_sni' in gateway
    assert '8081' not in docker and '8082' not in docker and '8083' not in docker
    assert 'EXPOSE 8080' in docker


def test_single_listener_routes_four_nodes():
    gateway = Path("scripts/gateway.py").read_text()
    assert 'server=await asyncio.start_server(handle,"0.0.0.0",PORT' in gateway
    assert 'REALITY_DEST=("127.0.0.1",10087)' in gateway
    assert 'GRPC_DEST=("127.0.0.1",10088)' in gateway
    assert 'WS_DEST=("127.0.0.1",10089)' in gateway
    assert 'if sni==RAW_SNI' in gateway
    assert 'if sni==GRPC_SNI' in gateway
    assert 'if WS_SNI and sni==WS_SNI' in gateway


def test_subscription_is_exactly_four():
    generate = Path("scripts/generate.py").read_text()
    assert 'if len(lines)!=NODE_COUNT' in generate
    assert '"node_count":4' in generate
