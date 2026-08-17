#!/usr/bin/env python3
import hashlib, json, os, re, secrets, urllib.parse
from pathlib import Path

# V54 SINGLE-8080 ROUTER: every Railway public entry targets application port 8080.
# 443 Domain -> 8080 -> plaintext HTTP -> 10086 XHTTP
# TCP Proxy #1 -> 8080 -> SNI RAW REALITY -> 10087 Vision
# TCP Proxy #2 -> 8080 -> SNI gRPC REALITY -> 10088 gRPC
# TCP Proxy #3 -> 8080 -> SNI WS TLS -> 10089 WS
D=Path(os.environ.get("DATA_DIR","/data")); D.mkdir(parents=True,exist_ok=True)
C=Path(os.environ.get("XRAY_CONFIG","/etc/xray/config.json"))
UUID=os.environ["UUID"].strip(); PRIVATE_KEY=os.environ["PRIVATE_KEY"].strip(); PUBLIC_KEY=os.environ["PUBLIC_KEY"].strip(); PUBLIC_DOMAIN=os.environ["PUBLIC_DOMAIN"].strip()
NODE_COUNT=4; APP_PORT=8080
DEFAULTS={"RAILWAY_TCP_PROXY":("reseau.proxy.rlwy.net","23337"),"RAILWAY_TCP_PROXY_2":("interchange.proxy.rlwy.net","23389"),"RAILWAY_TCP_PROXY_3":("altaria.proxy.rlwy.net","17903")}
def proxy(prefix):
    dh,dp=DEFAULTS[prefix];h=os.environ.get(prefix+"_DOMAIN",dh).strip() or dh;p=os.environ.get(prefix+"_PORT",dp).strip() or dp
    try:p=int(p)
    except ValueError:raise SystemExit(f"FATAL: invalid {prefix} port")
    if not h or not 1<=p<=65535:raise SystemExit(f"FATAL: invalid {prefix} endpoint")
    return {"domain":h,"port":p,"application_port":APP_PORT}
p1=proxy("RAILWAY_TCP_PROXY");p2=proxy("RAILWAY_TCP_PROXY_2");p3=proxy("RAILWAY_TCP_PROXY_3")
FP=os.environ.get("REALITY_FINGERPRINT","chrome").strip() or "chrome"
RAW_SNI=os.environ.get("REALITY_RAW_SNI","www.cloudflare.com").strip() or "www.cloudflare.com"
GRPC_SNI=os.environ.get("REALITY_GRPC_SNI","www.apple.com").strip() or "www.apple.com"
RAW_TARGET=os.environ.get("REALITY_RAW_TARGET","www.cloudflare.com:443").strip()
GRPC_TARGET=os.environ.get("REALITY_GRPC_TARGET","www.apple.com:443").strip()
XPATH=os.environ.get("XHTTP_PATH","/xhttp").strip() or "/xhttp";GRPC_SERVICE=os.environ.get("GRPC_SERVICE_NAME","grpc-service").strip() or "grpc-service";WS_PATH=os.environ.get("WS_PATH","/ws").strip() or "/ws";WS_HOST=os.environ.get("WS_HOST",p3["domain"]).strip() or p3["domain"]
WS_CERT=D/"ws_tls_cert.pem";WS_KEY=D/"ws_tls_key.pem";ids_file=D/"reality_short_ids.json"
try:ids=json.loads(ids_file.read_text()) if ids_file.exists() else []
except Exception:ids=[]
ids=[str(x) for x in ids if re.fullmatch(r"[0-9a-fA-F]{2,32}",str(x))]
while len(ids)<2:ids.append(secrets.token_hex(6))
ids=ids[:2];ids_file.write_text(json.dumps(ids,indent=2)+"\n")

def reality(tag,port,flow,sni,target,sid,network):
    client={"id":UUID,"level":0};
    if flow:client["flow"]=flow
    ss={"network":network,"security":"reality","realitySettings":{"show":False,"target":target,"serverNames":[sni],"privateKey":PRIVATE_KEY,"shortIds":[sid]}}
    if network=="grpc":ss["grpcSettings"]={"serviceName":GRPC_SERVICE,"multiMode":False}
    return {"tag":tag,"listen":"127.0.0.1","port":port,"protocol":"vless","settings":{"clients":[client],"decryption":"none"},"streamSettings":ss}
reality_raw=reality("vless-reality-vision",10087,"xtls-rprx-vision",RAW_SNI,RAW_TARGET,ids[0],"tcp")
reality_grpc=reality("vless-reality-grpc",10088,"",GRPC_SNI,GRPC_TARGET,ids[1],"grpc")
xhttp={"tag":"vless-xhttp-tls","listen":"127.0.0.1","port":10086,"protocol":"vless","settings":{"clients":[{"id":UUID,"level":0}],"decryption":"none"},"streamSettings":{"network":"xhttp","security":"none","xhttpSettings":{"path":XPATH,"mode":"auto"}}}
ws_tls={"tag":"vless-ws-tls","listen":"127.0.0.1","port":10089,"protocol":"vless","settings":{"clients":[{"id":UUID,"level":0}],"decryption":"none"},"streamSettings":{"network":"ws","security":"tls","tlsSettings":{"alpn":["http/1.1"],"certificates":[{"certificateFile":str(WS_CERT),"keyFile":str(WS_KEY)}]},"wsSettings":{"path":WS_PATH,"headers":{"Host":WS_HOST}}}}
cfg={"log":{"loglevel":os.environ.get("XRAY_LOGLEVEL","warning")},"policy":{"levels":{"0":{"handshake":8,"connIdle":900,"uplinkOnly":2,"downlinkOnly":5}}},"inbounds":[xhttp,reality_raw,reality_grpc,ws_tls],"outbounds":[{"tag":"direct","protocol":"freedom"},{"tag":"block","protocol":"blackhole"}]}
C.write_text(json.dumps(cfg,indent=2)+"\n")
def q(params):return urllib.parse.urlencode({k:str(v) for k,v in params.items() if v not in (None,"")},safe="")
def link(host,port,params,name):return f"vless://{UUID}@{host}:{port}?{q(params)}#{urllib.parse.quote(name,safe='')}"
lines=[
link(PUBLIC_DOMAIN,443,{"encryption":"none","security":"tls","sni":PUBLIC_DOMAIN,"fp":FP,"alpn":"h2,http/1.1","type":"xhttp","path":XPATH,"mode":"auto"},"VLESS XHTTP TLS · Railway Domain"),
link(p1["domain"],p1["port"],{"encryption":"none","flow":"xtls-rprx-vision","security":"reality","sni":RAW_SNI,"fp":FP,"pbk":PUBLIC_KEY,"sid":ids[0],"type":"tcp"},"VLESS RAW REALITY Vision · TCP Proxy 1"),
link(p2["domain"],p2["port"],{"encryption":"none","security":"reality","sni":GRPC_SNI,"fp":FP,"pbk":PUBLIC_KEY,"sid":ids[1],"type":"grpc","serviceName":GRPC_SERVICE,"mode":"gun"},"VLESS gRPC REALITY · TCP Proxy 2"),
link(p3["domain"],p3["port"],{"encryption":"none","security":"tls","sni":WS_HOST,"fp":FP,"alpn":"http/1.1","type":"ws","host":WS_HOST,"path":WS_PATH,"allowInsecure":"1"},"VLESS WS TLS · TCP Proxy 3")]
if len(lines)!=NODE_COUNT:raise SystemExit(f"FATAL: subscription invariant failed: expected {NODE_COUNT}, got {len(lines)}")
state={"schema":11,"build":"fixed-single-8080-router-v1","architecture":"single-8080-protocol-router","node_count":4,"public_domain":PUBLIC_DOMAIN,"application_port":8080,"router":{"http":10086,"raw_reality":10087,"grpc_reality":10088,"ws_tls":10089,"sni":{"raw":RAW_SNI,"grpc":GRPC_SNI,"ws":WS_HOST}},"tcp_proxies":[p1,p2,p3],"reality":{"raw":{"target":RAW_TARGET,"sni":RAW_SNI,"short_id":ids[0]},"grpc":{"target":GRPC_TARGET,"sni":GRPC_SNI,"short_id":ids[1]}},"ws":{"host":WS_HOST,"path":WS_PATH},"grpc":{"service_name":GRPC_SERVICE}}
fingerprint=hashlib.sha256(json.dumps(state,sort_keys=True,separators=(",",":")).encode()).hexdigest();state["fingerprint"]=fingerprint
(D/"state.json").write_text(json.dumps(state,indent=2)+"\n");(D/"subscription.txt.tmp").write_text("\n".join(lines)+"\n");os.replace(D/"subscription.txt.tmp",D/"subscription.txt")
(D/"manifest.json").write_text(json.dumps({"schema":11,"build":"fixed-single-8080-router-v1","node_count":4,"architecture":"single-8080-protocol-router","application_port":8080,"distribution":{"443":"xhttp-tls","tcp-proxy-1":"sni-raw-reality","tcp-proxy-2":"sni-grpc-reality","tcp-proxy-3":"sni-ws-tls"},"state_fingerprint":fingerprint},indent=2)+"\n")
print("RELEASE=fixed-single-8080-router-v1",flush=True);print("ARCHITECTURE=single-8080-protocol-router",flush=True);print("SUBSCRIPTION_INVARIANT=4",flush=True);print("PUBLIC_DOMAIN -> 8080 -> 10086 XHTTP TLS",flush=True);print(f"{p1['domain']}:{p1['port']} -> 8080 -> SNI {RAW_SNI} -> 10087 REALITY Vision",flush=True);print(f"{p2['domain']}:{p2['port']} -> 8080 -> SNI {GRPC_SNI} -> 10088 gRPC REALITY",flush=True);print(f"{p3['domain']}:{p3['port']} -> 8080 -> SNI {WS_HOST} -> 10089 WS TLS",flush=True);print("NODES=4",flush=True)
