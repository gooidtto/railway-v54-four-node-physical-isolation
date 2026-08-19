import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_release_manifest_is_four_node_capable():
    m = json.loads((ROOT / "RELEASE-MANIFEST.json").read_text())
    assert m["base_nodes"] == 3
    assert m["max_nodes"] == 4
    assert "cloudflare" in m["entries"]

def test_generator_contains_four_node_gate():
    s = (ROOT / "scripts" / "generate.py").read_text()
    assert "CF_VARIABLES" in s
    assert "CF_ENABLED = all(CF_VARIABLES)" in s
    assert "NODE_COUNT = len(lines)" in s

def test_gateway_has_fragmentation_logic():
    s = (ROOT / "scripts" / "gateway.py").read_text()
    assert "_tls_client_hello" in s
    assert "TLS_SNI_EARLY" in s
    assert "GATEWAY_MAX_CONNECTIONS" in s

def test_startup_has_uuid_invariant():
    s = (ROOT / "scripts" / "start.sh").read_text()
    assert "UUID_INVARIANT=OK" in s
    assert "SUBSCRIPTION_COUNT" in s
