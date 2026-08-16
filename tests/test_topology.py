from pathlib import Path

def test_expected_ports_are_present():
    text = Path("scripts/start.sh").read_text()
    assert 'VISION_PUBLIC_HOST' in text
    assert 'XHTTP_REALITY_PUBLIC_HOST' in text
    assert 'GRPC_REALITY_PUBLIC_HOST' in text
    assert '8081' in text and '8082' in text and '8083' in text
    assert '10086' in text

def test_stale_endpoint_is_rejected():
    text = Path("scripts/start.sh").read_text()
    assert 'altaria.proxy.rlwy.net:32227' in text
