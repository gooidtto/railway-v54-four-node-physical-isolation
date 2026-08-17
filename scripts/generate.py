#!/usr/bin/env python3
import hashlib, json, os, re, secrets, urllib.parse
from pathlib import Path

# V54 PHYSICAL-ISOLATION: exactly four independent paths.
# 443 -> 8080 -> 10086 : VLESS XHTTP (Railway TLS edge)
# TCP1 -> 8081 -> 10087 : VLESS RAW/TCP + REALITY + Vision
# TCP2 -> 8082 -> 10088 : VLESS gRPC + REALITY
# TCP3 -> 8083 -> 10089 : VLESS WS + TLS
D=Path(os.environ.get("DATA_DIR","/data")); D.mkdir(parents=True,exist_ok=True)
C=Path(os.environ.get("XRAY_CONFIG","/etc/xray/config.json"))
UUID=os.environ["UUID"].strip(); PRIVATE_KEY=os.environ["PRIVATE_KEY"].strip(); PUBLIC_KEY=os.environ["PUBLIC_KEY"].strip(); PUBLIC_DOMAIN=os.environ["PUBLIC_DOMAIN"].strip()
NODE_COUNT=4

def required_proxy(prefix,expected_app):
    h=os.environ.get(prefix+"_DOMAIN","").strip(); p=os.environ.get(prefix+"_PORT","").strip(); a=os.environ.get(prefix+"_APPLICATION_PORT","").strip()
    if not h or not p or not a: raise SystemExit(f"FATAL: {prefix} runtime variables are required")
    try:p,a=int(p),int(a)
    except ValueError: raise SystemExit(f"FATAL: invalid {prefix} port")
    if not 1<=p<=65535 or a!=expected_app: raise SystemExit(f"FATAL: {prefix} must target application port {expected_app}")
    return {"domain":h,"port":p,"application_port":a}

p1=required_proxy("RAILWAY_TCP_PROXY",8081); p2=required_proxy("RAILWAY_TCP_PROXY_2",8082); p3=required_proxy("RAILWAY_TCP_PROXY_3",8083)
REALITY_TARGET=os.environ.get("REALITY_TARGET","www.cloudflare.com:443").strip()
SNI_FILE=Path(os.environ.get("REALITY_SNI_CANDIDATES_FILE","/opt/xray/config/reality-sni-candidates.txt"))
snis=[x.strip() for x in SNI_FILE.read_text().splitlines() if x.strip()]
if len(snis)!=1: raise SystemExit("FATAL: REALITY SNI baseline requires exactly one SNI")
REALITY_SNI=snis[0]; FP=os.environ.get("REALITY_FINGERPRINT","chrome").strip() or "chrome"; XPATH=os.environ.get("XHTTP_PATH","/xhttp").strip() or "/xhttp"; GRPC_SERVICE=os.environ.get("GRPC_SERVICE_NAME","grpc-service").strip() or "grpc-service"; WS_PATH=os.environ.get("WS_PATH","/ws").strip() or "/ws"; WS_HOST=os.environ.get("WS_HOST",p3["domain"]).strip() or p3["domain"]
WS_CERT=D/"ws_tls_cert.pem"; WS_KEY=D/"ws_tls_key.pem"
ids_file=D/"reality_short_ids.json"
try: ids=json.loads(ids_file.read_text()) if ids_file.exists() else []
except Exception: ids=[]
ids=[str(x) for x in ids if re.fullmatch(r"[0-9a-fA-F]{2,32}",str(x))]
while len(ids)<2: ids.append(secrets.token_hex(6))
ids=ids[:2]; ids_file.write_text(json.dumps(ids,indent=2)+"\n")

reality_raw={"tag":"vless-reality-vision","listen":"127.0.0.1","port":10087,"protocol":"vless","settings":{"clients":[{"id":UUID,"level":0,"flow":"xtls-rprx-vision"}],"decryption":"none"},"streamSettings":{"network":"tcp","security":"reality","realitySettings":{"show":False,"target":REALITY_TARGET,"serverNames":[REALITY_SNI],"privateKey":PRIVATE_KEY,"shortIds":[ids[0]]}}}
reality_grpc={"tag":"vless-reality-grpc","listen":"127.0.0.1","port":10088,"protocol":"vless","settings":{"clients":[{"id":UUID,"level":0}],"decryption":"none"},"streamSettings":{"network":"grpc","security":"reality","realitySettings":{"show":False,"target":REALITY_TARGET,"serverNames":[REALITY_SNI],"privateKey":PRIVATE_KEY,"shortIds":[ids[1]]},"grpcSettings":{"serviceName":GRPC_SERVICE,"multiMode":False}}}
xhttp={"tag":"vless-xhttp-tls","listen":"127.0.0.1","port":10086,"protocol":"vless","settings":{"clients":[{"id":UUID,"level":0}],"decryption":"none"},"streamSettings":{"network":"xhttp","security":"none","xhttpSettings":{"path":XPATH,"mode":"auto"}}}
ws_tls={"tag":"vless-ws-tls","listen":"127.0.0.1","port":10089,"protocol":"vless","settings":{"clients":[{"id":UUID,"level":0}],"decryption":"none"},"streamSettings":{"network":"ws","security":"tls","tlsSettings":{"alpn":["http/1.1"],"certificates":[{"certificateFile":str(WS_CERT),"keyFile":str(WS_KEY)}]},"wsSettings":{"path":WS_PATH,"headers":{"Host":WS_HOST}}}}
cfg={"log":{"loglevel":os.environ.get("XRAY_LOGLEVEL","warning")},"policy":{"levels":{"0":{"handshake":8,"connIdle":900,"uplinkOnly":2,"downlinkOnly":5}}},"inbounds":[xhttp,reality_raw,reality_grpc,ws_tls],"outbounds":[{"tag":"direct","protocol":"freedom"},{"tag":"block","protocol":"blackhole"}]}
C.write_text(json.dumps(cfg,indent=2)+"\n")

def q(params): return urllib.parse.urlencode({k:str(v) for k,v in params.items() if v not in (None,"")},safe="")
def link(host,port,params,name): return f"vless://{UUID}@{host}:{port}?{q(params)}#{urllib.parse.quote(name,safe='')}"
lines=[
link(PUBLIC_DOMAIN,443,{"encryption":"none","security":"tls","sni":PUBLIC_DOMAIN,"fp":FP,"alpn":"h2,http/1.1","type":"xhttp","path":XPATH,"mode":"auto"},"VLESS XHTTP TLS · Railway Domain"),
link(p1["domain"],p1["port"],{"encryption":"none","flow":"xtls-rprx-vision","security":"reality","sni":REALITY_SNI,"fp":FP,"pbk":PUBLIC_KEY,"sid":ids[0],"type":"tcp"},"VLESS RAW REALITY Vision · TCP 8081"),
link(p2["domain"],p2["port"],{"encryption":"none","security":"reality","sni":REALITY_SNI,"fp":FP,"pbk":PUBLIC_KEY,"sid":ids[1],"type":"grpc","serviceName":GRPC_SERVICE,"mode":"gun"},"VLESS gRPC REALITY · TCP 8082"),
link(p3["domain"],p3["port"],{"encryption":"none","security":"tls","sni":WS_HOST,"fp":FP,"alpn":"http/1.1","type":"ws","host":WS_HOST,"path":WS_PATH,"allowInsecure":"1"},"VLESS WS TLS · TCP 8083")]
if len(lines)!=NODE_COUNT: raise SystemExit(f"FATAL: subscription invariant failed: expected {NODE_COUNT}, got {len(lines)}")
for i,line in enumerate(lines,1):
    u=urllib.parse.urlsplit(line)
    if u.scheme!="vless" or not u.hostname or not u.port: raise SystemExit(f"FATAL: node {i}: invalid URI")
state={"schema":9,"build":"fixed-4-node-physical-isolation-v5","architecture":"physical-four-entry","node_count":4,"public_domain":PUBLIC_DOMAIN,"gateway":{"8080":10086,"8081":10087,"8082":10088,"8083":10089},"tcp_proxies":[p1,p2,p3],"xray_inbounds":{"xhttp_tls":10086,"reality_raw":10087,"reality_grpc":10088,"ws_tls":10089},"reality":{"target":REALITY_TARGET,"sni":REALITY_SNI,"short_ids":ids},"ws":{"host":WS_HOST,"path":WS_PATH},"grpc":{"service_name":GRPC_SERVICE}}
fingerprint=hashlib.sha256(json.dumps(state,sort_keys=True,separators=(",",":")).encode()).hexdigest(); state["fingerprint"]=fingerprint
(D/"state.json").write_text(json.dumps(state,indent=2)+"\n"); (D/"subscription.txt.tmp").write_text("\n".join(lines)+"\n"); os.replace(D/"subscription.txt.tmp",D/"subscription.txt")
(D/"manifest.json").write_text(json.dumps({"schema":9,"build":"fixed-4-node-physical-isolation-v5","node_count":4,"architecture":"physical-four-entry","distribution":{"443":"xhttp-tls","8081":"raw-reality-vision","8082":"grpc-reality","8083":"ws-tls"},"state_fingerprint":fingerprint},indent=2)+"\n")
print("RELEASE=fixed-4-node-physical-isolation-v5",flush=True); print("ARCHITECTURE=physical-four-entry",flush=True); print("SUBSCRIPTION_INVARIANT=4",flush=True); print("443 -> 8080 -> 10086 XHTTP TLS",flush=True); print(f"{p1['domain']}:{p1['port']} -> 8081 -> 10087 RAW REALITY Vision",flush=True); print(f"{p2['domain']}:{p2['port']} -> 8082 -> 10088 gRPC REALITY",flush=True); print(f"{p3['domain']}:{p3['port']} -> 8083 -> 10089 WS TLS",flush=True); print("NODES=4",flush=True)
