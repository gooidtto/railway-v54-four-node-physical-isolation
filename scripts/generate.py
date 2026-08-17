#!/usr/bin/env python3
import hashlib
import json
import os
import re
import secrets
import urllib.parse
from pathlib import Path

D = Path(os.environ.get("DATA_DIR", "/data")); D.mkdir(parents=True, exist_ok=True)
C = Path(os.environ.get("XRAY_CONFIG", "/etc/xray/config.json"))
UUID = os.environ["UUID"].strip(); PRIVATE_KEY = os.environ["PRIVATE_KEY"].strip(); PUBLIC_KEY = os.environ["PUBLIC_KEY"].strip(); PUBLIC_DOMAIN = os.environ["PUBLIC_DOMAIN"].strip()

# This release is intentionally and permanently four-node.
NODE_COUNT = 4

def proxy(prefix, dh, dp, da):
    h = os.environ.get(prefix + "_DOMAIN", dh).strip(); p = os.environ.get(prefix + "_PORT", dp).strip(); a = os.environ.get(prefix + "_APPLICATION_PORT", da).strip()
    try: p, a = int(p), int(a)
    except ValueError: raise SystemExit(f"invalid {prefix} port")
    if not h or not 1 <= p <= 65535: raise SystemExit(f"invalid {prefix}")
    return {"domain": h, "port": p, "application_port": a}

p1 = proxy("RAILWAY_TCP_PROXY", "reseau.proxy.rlwy.net", "23337", "8081")
p2 = proxy("RAILWAY_TCP_PROXY_2", "interchange.proxy.rlwy.net", "23389", "8082")
p3 = proxy("RAILWAY_TCP_PROXY_3", "altaria.proxy.rlwy.net", "17903", "8083")
if [p1["application_port"], p2["application_port"], p3["application_port"]] != [8081, 8082, 8083]:
    raise SystemExit("TCP proxy targets must be 8081,8082,8083")

REALITY_TARGET = os.environ.get("REALITY_TARGET", "www.cloudflare.com:443").strip()
SNI_FILE = Path(os.environ.get("REALITY_SNI_CANDIDATES_FILE", "/opt/xray/config/reality-sni-candidates.txt"))
snis = [x.strip() for x in SNI_FILE.read_text().splitlines() if x.strip()]
if len(snis) != 1: raise SystemExit("REALITY SNI baseline requires exactly one SNI")
REALITY_SNI = snis[0]
FP = os.environ.get("REALITY_FINGERPRINT", "chrome").strip() or "chrome"
XPATH = os.environ.get("XHTTP_PATH", "/xhttp").strip() or "/xhttp"
GRPC_SERVICE = os.environ.get("GRPC_SERVICE_NAME", "grpc-service").strip() or "grpc-service"
WS_PATH = os.environ.get("WS_PATH", "/ws").strip() or "/ws"
WS_HOST = os.environ.get("WS_HOST", p3["domain"]).strip() or p3["domain"]
WS_CERT = D / "ws_tls_cert.pem"; WS_KEY = D / "ws_tls_key.pem"

short_file = D / "short_id.txt"; legacy = short_file.read_text().strip() if short_file.exists() else ""
ids_file = D / "reality_short_ids.json"
try: ids = json.loads(ids_file.read_text()) if ids_file.exists() else []
except Exception: ids = []
ids = [str(x) for x in ids if re.fullmatch(r"[0-9a-fA-F]{2,32}", str(x))]
if legacy and legacy not in ids: ids.insert(0, legacy)
while len(ids) < 2: ids.append(secrets.token_hex(6))
ids = ids[:2]
ids_file.write_text(json.dumps(ids, indent=2) + "\n"); short_file.write_text(ids[0] + "\n")

reality_raw = {"tag":"vless-reality-vision","listen":"127.0.0.1","port":10087,"protocol":"vless","settings":{"clients":[{"id":UUID,"level":0,"flow":"xtls-rprx-vision"}],"decryption":"none"},"streamSettings":{"network":"tcp","security":"reality","realitySettings":{"show":False,"target":REALITY_TARGET,"serverNames":[REALITY_SNI],"privateKey":PRIVATE_KEY,"shortIds":[ids[0]]}}}
reality_grpc = {"tag":"vless-reality-grpc","listen":"127.0.0.1","port":10088,"protocol":"vless","settings":{"clients":[{"id":UUID,"level":0}],"decryption":"none"},"streamSettings":{"network":"grpc","security":"reality","realitySettings":{"show":False,"target":REALITY_TARGET,"serverNames":[REALITY_SNI],"privateKey":PRIVATE_KEY,"shortIds":[ids[1]]},"grpcSettings":{"serviceName":GRPC_SERVICE,"multiMode":False}}}
xhttp = {"tag":"vless-xhttp-tls","listen":"127.0.0.1","port":10086,"protocol":"vless","settings":{"clients":[{"id":UUID,"level":0}],"decryption":"none"},"streamSettings":{"network":"xhttp","security":"none","xhttpSettings":{"path":XPATH,"mode":"auto"}}}
ws_tls = {"tag":"vless-ws-tls","listen":"127.0.0.1","port":10089,"protocol":"vless","settings":{"clients":[{"id":UUID,"level":0}],"decryption":"none"},"streamSettings":{"network":"ws","security":"tls","tlsSettings":{"alpn":["http/1.1"],"certificates":[{"certificateFile":str(WS_CERT),"keyFile":str(WS_KEY)}]},"wsSettings":{"path":WS_PATH,"headers":{"Host":WS_HOST}}}

cfg={"log":{"loglevel":os.environ.get("XRAY_LOGLEVEL","warning")},"policy":{"levels":{"0":{"handshake":8,"connIdle":900,"uplinkOnly":2,"downlinkOnly":5}}},"inbounds":[reality_raw,reality_grpc,xhttp,ws_tls],"outbounds":[{"tag":"direct","protocol":"freedom"},{"tag":"block","protocol":"blackhole"}]}
C.write_text(json.dumps(cfg,indent=2)+"\n")

def q(params): return urllib.parse.urlencode({k:str(v) for k,v in params.items() if v not in (None,"")},safe="")
def link(host,port,params,name): return f"vless://{UUID}@{host}:{port}?{q(params)}#{urllib.parse.quote(name,safe='')}"

lines=[
 link(PUBLIC_DOMAIN,443,{"encryption":"none","security":"tls","sni":PUBLIC_DOMAIN,"fp":FP,"alpn":"h2,http/1.1","type":"xhttp","path":XPATH,"mode":"auto"},"VLESS XHTTP TLS · Railway Domain"),
 link(p1["domain"],p1["port"],{"encryption":"none","flow":"xtls-rprx-vision","security":"reality","sni":REALITY_SNI,"fp":FP,"pbk":PUBLIC_KEY,"sid":ids[0],"type":"tcp"},f"VLESS RAW REALITY Vision · TCP {p1['application_port']}"),
 link(p2["domain"],p2["port"],{"encryption":"none","security":"reality","sni":REALITY_SNI,"fp":FP,"pbk":PUBLIC_KEY,"sid":ids[1],"type":"grpc","serviceName":GRPC_SERVICE,"mode":"gun"},f"VLESS gRPC REALITY · TCP {p2['application_port']}"),
 link(p3["domain"],p3["port"],{"encryption":"none","security":"tls","sni":WS_HOST,"fp":FP,"alpn":"http/1.1","type":"ws","host":WS_HOST,"path":WS_PATH,"allowInsecure":"1"},f"VLESS WS TLS · TCP {p3['application_port']}"),
]
if len(lines) != NODE_COUNT: raise SystemExit(f"subscription invariant failed: expected {NODE_COUNT}, got {len(lines)}")

for i,line in enumerate(lines,1):
    u=urllib.parse.urlsplit(line)
    if u.scheme!="vless" or not u.hostname or not u.port: raise SystemExit(f"node {i}: invalid URI")

state={"schema":8,"build":"fixed-4-node-physical-isolation-v2","node_count":NODE_COUNT,"public_domain":PUBLIC_DOMAIN,"tcp_proxies":[p1,p2,p3],"gateway_ports":[8080,8081,8082,8083],"xray_inbounds":{"xhttp_tls":10086,"reality_raw":10087,"reality_grpc":10088,"ws_tls":10089},"reality":{"target":REALITY_TARGET,"sni":REALITY_SNI,"short_ids":ids},"ws":{"host":WS_HOST,"path":WS_PATH},"grpc":{"service_name":GRPC_SERVICE}}
fingerprint=hashlib.sha256(json.dumps(state,sort_keys=True,separators=(",",":")).encode()).hexdigest();state["fingerprint"]=fingerprint
(D/"state.json").write_text(json.dumps(state,indent=2)+"\n")
(D/"subscription.txt.tmp").write_text("\n".join(lines)+"\n");os.replace(D/"subscription.txt.tmp",D/"subscription.txt")
(D/"manifest.json").write_text(json.dumps({"schema":8,"build":"fixed-4-node-physical-isolation-v2","node_count":NODE_COUNT,"distribution":{"443":"xhttp-tls","23337":"raw-reality-vision","23389":"grpc-reality","17903":"ws-tls"},"state_fingerprint":fingerprint},indent=2)+"\n")
print("BUILD=fixed-4-node-physical-isolation-v2",flush=True)
print("SUBSCRIPTION_INVARIANT=4",flush=True)
print(f"XHTTP_TLS={PUBLIC_DOMAIN}:443 -> 8080 -> 10086",flush=True)
print(f"RAW_REALITY={p1['domain']}:{p1['port']} -> 8081 -> 10087",flush=True)
print(f"GRPC_REALITY={p2['domain']}:{p2['port']} -> 8082 -> 10088",flush=True)
print(f"WS_TLS={p3['domain']}:{p3['port']} -> 8083 -> 10089",flush=True)
print("SUBSCRIPTION_NODES=4",flush=True)
