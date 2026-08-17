#!/usr/bin/env python3
import hashlib,json,os,re,secrets,urllib.parse
from pathlib import Path
D=Path(os.environ.get('DATA_DIR','/data'));D.mkdir(parents=True,exist_ok=True)
C=Path(os.environ.get('XRAY_CONFIG','/etc/xray/config.json'))
UUID=os.environ['UUID'].strip();PRIVATE_KEY=os.environ['PRIVATE_KEY'].strip();PUBLIC_KEY=os.environ['PUBLIC_KEY'].strip();PUBLIC_DOMAIN=os.environ['PUBLIC_DOMAIN'].strip()
APP_PORT=8080;NODE_COUNT=3
h=(os.environ.get('RAILWAY_TCP_PROXY_DOMAIN') or '').strip();p0=(os.environ.get('RAILWAY_TCP_PROXY_PORT') or '').strip()
if not h or not p0:raise SystemExit('FATAL: RAILWAY_TCP_PROXY_DOMAIN/PORT required')
try:p=int(p0)
except ValueError:raise SystemExit(f'FATAL: invalid RAILWAY_TCP_PROXY_PORT={p0!r}')
if not 1<=p<=65535:raise SystemExit('FATAL: invalid TCP proxy port')
FP=os.environ.get('REALITY_FINGERPRINT','chrome').strip() or 'chrome'
RAW_SNI=os.environ.get('REALITY_RAW_SNI','www.cloudflare.com').strip() or 'www.cloudflare.com'
XHTTP_SNI=os.environ.get('REALITY_XHTTP_SNI','www.apple.com').strip() or 'www.apple.com'
RAW_TARGET=os.environ.get('REALITY_RAW_TARGET','www.cloudflare.com:443').strip() or 'www.cloudflare.com:443'
XHTTP_TARGET=os.environ.get('REALITY_XHTTP_TARGET','www.apple.com:443').strip() or 'www.apple.com:443'
XPATH=os.environ.get('XHTTP_PATH','/xhttp').strip() or '/xhttp'
ids_file=D/'reality_short_ids.json'
try:ids=json.loads(ids_file.read_text()) if ids_file.exists() else []
except Exception:ids=[]
ids=[str(x) for x in ids if re.fullmatch(r'[0-9a-fA-F]{2,32}',str(x))]
while len(ids)<2:ids.append(secrets.token_hex(6))
ids=ids[:2];ids_file.write_text(json.dumps(ids,indent=2)+'\n')
def reality(tag,port,network,sni,target,sid,flow=''):
    client={'id':UUID,'level':0}
    if flow:client['flow']=flow
    ss={'network':network,'security':'reality','realitySettings':{'show':False,'target':target,'serverNames':[sni],'privateKey':PRIVATE_KEY,'shortIds':[sid]}}
    if network=='xhttp':ss['xhttpSettings']={'path':XPATH,'mode':'auto'}
    return {'tag':tag,'listen':'127.0.0.1','port':port,'protocol':'vless','settings':{'clients':[client],'decryption':'none'},'streamSettings':ss}
xhttp_tls={'tag':'vless-xhttp-tls','listen':'127.0.0.1','port':10086,'protocol':'vless','settings':{'clients':[{'id':UUID,'level':0}],'decryption':'none'},'streamSettings':{'network':'xhttp','security':'none','xhttpSettings':{'path':XPATH,'mode':'auto'}}}
raw=reality('vless-reality-vision',10087,'tcp',RAW_SNI,RAW_TARGET,ids[0],'xtls-rprx-vision')
xhttp_reality=reality('vless-xhttp-reality',10088,'xhttp',XHTTP_SNI,XHTTP_TARGET,ids[1])
config={'log':{'loglevel':os.environ.get('XRAY_LOGLEVEL','warning')},'policy':{'levels':{'0':{'handshake':8,'connIdle':900,'uplinkOnly':2,'downlinkOnly':5}}},'inbounds':[xhttp_tls,raw,xhttp_reality],'outbounds':[{'tag':'direct','protocol':'freedom'},{'tag':'block','protocol':'blackhole'}]}
C.write_text(json.dumps(config,indent=2)+'\n')
def q(d):return urllib.parse.urlencode({k:str(v) for k,v in d.items() if v not in (None,'')},safe='')
def link(host,port,params,name):return f'vless://{UUID}@{host}:{port}?{q(params)}#{urllib.parse.quote(name,safe="")}'
lines=[
link(PUBLIC_DOMAIN,443,{'encryption':'none','security':'tls','sni':PUBLIC_DOMAIN,'fp':FP,'alpn':'h2,http/1.1','type':'xhttp','path':XPATH,'mode':'auto'},'VLESS XHTTP TLS · Railway Domain'),
link(h,p,{'encryption':'none','flow':'xtls-rprx-vision','security':'reality','sni':RAW_SNI,'fp':FP,'pbk':PUBLIC_KEY,'sid':ids[0],'type':'tcp'},'VLESS RAW REALITY Vision · TCP Proxy'),
link(h,p,{'encryption':'none','security':'reality','sni':XHTTP_SNI,'fp':FP,'alpn':'h2','pbk':PUBLIC_KEY,'sid':ids[1],'type':'xhttp','path':XPATH,'mode':'auto'},'VLESS XHTTP REALITY · TCP Proxy')]
if len(lines)!=NODE_COUNT:raise SystemExit(f'FATAL: expected {NODE_COUNT} nodes, got {len(lines)}')
state={'schema':18,'build':'stable-3node-single-8080','architecture':'single-8080-router','node_count':3,'application_port':8080,'public_domain':PUBLIC_DOMAIN,'tcp_proxy':{'domain':h,'port':p,'application_port':8080},'routes':{'domain_xhttp_tls':{'port':10086},'raw_reality_vision':{'sni':RAW_SNI,'port':10087,'short_id':ids[0]},'xhttp_reality':{'sni':XHTTP_SNI,'port':10088,'short_id':ids[1]}},'xhttp_path':XPATH}
state['fingerprint']=hashlib.sha256(json.dumps(state,sort_keys=True,separators=(',',':')).encode()).hexdigest()
(D/'state.json').write_text(json.dumps(state,indent=2)+'\n');(D/'subscription.txt.tmp').write_text('\n'.join(lines)+'\n');os.replace(D/'subscription.txt.tmp',D/'subscription.txt')
(D/'manifest.json').write_text(json.dumps({'schema':18,'build':'stable-3node-single-8080','node_count':3,'application_port':8080,'distribution':{'01':'domain-xhttp-tls','02':'raw-reality-vision','03':'xhttp-reality'},'state_fingerprint':state['fingerprint']},indent=2)+'\n')
print('RELEASE=stable-3node-single-8080',flush=True);print('ARCHITECTURE=single-8080-router',flush=True);print('SUBSCRIPTION_INVARIANT=3',flush=True);print(f'DOMAIN {PUBLIC_DOMAIN}:443 -> 8080 -> 10086 XHTTP TLS',flush=True);print(f'TCP {h}:{p} -> 8080 -> {RAW_SNI} -> 10087 RAW REALITY Vision',flush=True);print(f'TCP {h}:{p} -> 8080 -> {XHTTP_SNI} -> 10088 XHTTP REALITY',flush=True);print('NODES=3',flush=True)
