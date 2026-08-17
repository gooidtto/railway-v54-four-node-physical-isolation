#!/usr/bin/env python3
import hashlib,json,os,re,secrets,urllib.parse
from pathlib import Path

# TEST 4-NODE / SINGLE-8080 ROUTER
# 01 TCP Proxy -> 8080 -> SNI RAW REALITY -> 10087 Vision
# 02 TCP Proxy -> 8080 -> SNI XHTTP REALITY -> 10088
# 03 TCP Proxy -> 8080 -> SNI RAW REALITY -> 10089 Vision
# 04 TCP Proxy -> 8080 -> SNI gRPC REALITY -> 10090
# Railway exposes only 8080. The gateway routes the TLS ClientHello by SNI.
D=Path(os.environ.get('DATA_DIR','/data'));D.mkdir(parents=True,exist_ok=True)
C=Path(os.environ.get('XRAY_CONFIG','/etc/xray/config.json'))
UUID=os.environ['UUID'].strip();PRIVATE_KEY=os.environ['PRIVATE_KEY'].strip();PUBLIC_KEY=os.environ['PUBLIC_KEY'].strip();PUBLIC_DOMAIN=os.environ['PUBLIC_DOMAIN'].strip()
APP_PORT=8080;NODE_COUNT=4
h=(os.environ.get('RAILWAY_TCP_PROXY_DOMAIN') or '').strip();p0=(os.environ.get('RAILWAY_TCP_PROXY_PORT') or '').strip()
if not h or not p0:raise SystemExit('FATAL: RAILWAY_TCP_PROXY_DOMAIN/PORT required')
try:p=int(p0)
except ValueError:raise SystemExit(f'FATAL: invalid RAILWAY_TCP_PROXY_PORT={p0!r}')
if not 1<=p<=65535:raise SystemExit('FATAL: invalid TCP proxy port')
FP=os.environ.get('REALITY_FINGERPRINT','chrome').strip() or 'chrome'
RAW1_SNI=os.environ.get('REALITY_RAW_SNI','www.cloudflare.com').strip() or 'www.cloudflare.com'
XHTTP_SNI=os.environ.get('REALITY_XHTTP_SNI','www.apple.com').strip() or 'www.apple.com'
RAW2_SNI=os.environ.get('REALITY_RAW2_SNI','www.bing.com').strip() or 'www.bing.com'
GRPC_SNI=os.environ.get('REALITY_GRPC_SNI','www.microsoft.com').strip() or 'www.microsoft.com'
RAW1_TARGET=os.environ.get('REALITY_RAW_TARGET','www.cloudflare.com:443').strip() or 'www.cloudflare.com:443'
XHTTP_TARGET=os.environ.get('REALITY_XHTTP_TARGET','www.apple.com:443').strip() or 'www.apple.com:443'
RAW2_TARGET=os.environ.get('REALITY_RAW2_TARGET','www.bing.com:443').strip() or 'www.bing.com:443'
GRPC_TARGET=os.environ.get('REALITY_GRPC_TARGET','www.microsoft.com:443').strip() or 'www.microsoft.com:443'
XPATH=os.environ.get('XHTTP_PATH','/xhttp').strip() or '/xhttp'
GRPC_SERVICE=os.environ.get('GRPC_SERVICE_NAME','grpc-service').strip() or 'grpc-service'
ids_file=D/'reality_short_ids.json'
try:ids=json.loads(ids_file.read_text()) if ids_file.exists() else []
except Exception:ids=[]
ids=[str(x) for x in ids if re.fullmatch(r'[0-9a-fA-F]{2,32}',str(x))]
while len(ids)<4:ids.append(secrets.token_hex(6))
ids=ids[:4];ids_file.write_text(json.dumps(ids,indent=2)+'\n')

def inbound(tag,port,network,sni,target,sid,flow=''):
    client={'id':UUID,'level':0}
    if flow:client['flow']=flow
    ss={'network':network,'security':'reality','realitySettings':{'show':False,'target':target,'serverNames':[sni],'privateKey':PRIVATE_KEY,'shortIds':[sid]}}
    if network=='xhttp':ss['xhttpSettings']={'path':XPATH,'mode':'auto'}
    if network=='grpc':ss['grpcSettings']={'serviceName':GRPC_SERVICE,'multiMode':False}
    return {'tag':tag,'listen':'127.0.0.1','port':port,'protocol':'vless','settings':{'clients':[client],'decryption':'none'},'streamSettings':ss}

# Internal Xray listeners. Railway itself only exposes 8080.
xhttp_tls={'tag':'vless-xhttp-http','listen':'127.0.0.1','port':10086,'protocol':'vless','settings':{'clients':[{'id':UUID,'level':0}],'decryption':'none'},'streamSettings':{'network':'xhttp','security':'none','xhttpSettings':{'path':XPATH,'mode':'auto'}}}
raw1=inbound('vless-reality-vision-01',10087,'tcp',RAW1_SNI,RAW1_TARGET,ids[0],'xtls-rprx-vision')
xhttp_reality=inbound('vless-xhttp-reality',10088,'xhttp',XHTTP_SNI,XHTTP_TARGET,ids[1])
raw2=inbound('vless-reality-vision-02',10089,'tcp',RAW2_SNI,RAW2_TARGET,ids[2],'xtls-rprx-vision')
grpc=inbound('vless-grpc-reality',10090,'grpc',GRPC_SNI,GRPC_TARGET,ids[3])
config={'log':{'loglevel':os.environ.get('XRAY_LOGLEVEL','warning')},'policy':{'levels':{'0':{'handshake':8,'connIdle':900,'uplinkOnly':2,'downlinkOnly':5}}},'inbounds':[xhttp_tls,raw1,xhttp_reality,raw2,grpc],'outbounds':[{'tag':'direct','protocol':'freedom'},{'tag':'block','protocol':'blackhole'}]}
C.write_text(json.dumps(config,indent=2)+'\n')

def q(d):return urllib.parse.urlencode({k:str(v) for k,v in d.items() if v not in (None,'')},safe='')
def link(host,port,params,name):return f'vless://{UUID}@{host}:{port}?{q(params)}#{urllib.parse.quote(name,safe="")}'
lines=[
 link(h,p,{'encryption':'none','flow':'xtls-rprx-vision','security':'reality','sni':RAW1_SNI,'fp':FP,'pbk':PUBLIC_KEY,'sid':ids[0],'type':'tcp'},'VLESS RAW REALITY Vision 01 · TCP Proxy'),
 link(h,p,{'encryption':'none','security':'reality','sni':XHTTP_SNI,'fp':FP,'pbk':PUBLIC_KEY,'sid':ids[1],'type':'xhttp','path':XPATH,'mode':'auto'},'VLESS XHTTP REALITY 02 · TCP Proxy'),
 link(h,p,{'encryption':'none','flow':'xtls-rprx-vision','security':'reality','sni':RAW2_SNI,'fp':FP,'pbk':PUBLIC_KEY,'sid':ids[2],'type':'tcp'},'VLESS RAW REALITY Vision 03 · TCP Proxy'),
 link(h,p,{'encryption':'none','security':'reality','sni':GRPC_SNI,'fp':FP,'pbk':PUBLIC_KEY,'sid':ids[3],'type':'grpc','serviceName':GRPC_SERVICE,'mode':'gun'},'VLESS gRPC REALITY 04 · TCP Proxy')]
if len(lines)!=NODE_COUNT:raise SystemExit(f'FATAL: expected {NODE_COUNT} nodes, got {len(lines)}')
state={'schema':16,'build':'test-4node-single-8080-grpc-reality','architecture':'single-8080-sni-router','node_count':4,'application_port':8080,'tcp_proxy':{'domain':h,'port':p,'application_port':8080},'routes':{'raw1':{'sni':RAW1_SNI,'port':10087,'short_id':ids[0]},'xhttp':{'sni':XHTTP_SNI,'port':10088,'short_id':ids[1]},'raw2':{'sni':RAW2_SNI,'port':10089,'short_id':ids[2]},'grpc':{'sni':GRPC_SNI,'port':10090,'short_id':ids[3],'service_name':GRPC_SERVICE}},'xhttp_path':XPATH}
state['fingerprint']=hashlib.sha256(json.dumps(state,sort_keys=True,separators=(',',':')).encode()).hexdigest()
(D/'state.json').write_text(json.dumps(state,indent=2)+'\n');(D/'subscription.txt.tmp').write_text('\n'.join(lines)+'\n');os.replace(D/'subscription.txt.tmp',D/'subscription.txt')
(D/'manifest.json').write_text(json.dumps({'schema':16,'build':'test-4node-single-8080-grpc-reality','node_count':4,'application_port':8080,'distribution':{'raw1':'raw-reality-vision','xhttp':'xhttp-reality','raw2':'raw-reality-vision','grpc':'grpc-reality'},'state_fingerprint':state['fingerprint']},indent=2)+'\n')
print('RELEASE=test-4node-single-8080-grpc-reality',flush=True);print('SUBSCRIPTION_INVARIANT=4',flush=True);print(f'TCP {h}:{p} -> 8080 -> {RAW1_SNI} -> 10087 RAW REALITY Vision',flush=True);print(f'TCP {h}:{p} -> 8080 -> {XHTTP_SNI} -> 10088 XHTTP REALITY',flush=True);print(f'TCP {h}:{p} -> 8080 -> {RAW2_SNI} -> 10089 RAW REALITY Vision',flush=True);print(f'TCP {h}:{p} -> 8080 -> {GRPC_SNI} -> 10090 gRPC REALITY',flush=True);print('NODES=4',flush=True)
