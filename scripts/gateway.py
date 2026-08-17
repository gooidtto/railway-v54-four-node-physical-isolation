#!/usr/bin/env python3
import asyncio,base64,os,re,struct,urllib.parse
from pathlib import Path
PORT=int(os.environ.get('GATEWAY_PORT','8080'));D=Path(os.environ.get('DATA_DIR','/data'));SITE=Path('/opt/xray/site/index.html')
TOKEN=D/'subscription_token.txt';SUB=D/'subscription.txt';HTTP_DEST=('127.0.0.1',10086)
ROUTES={
 os.environ.get('REALITY_RAW_SNI','www.cloudflare.com').strip() or 'www.cloudflare.com':('127.0.0.1',10087,'raw-reality-vision'),
 os.environ.get('REALITY_XHTTP_SNI','www.apple.com').strip() or 'www.apple.com':('127.0.0.1',10088,'xhttp-reality'),
}
SEM=asyncio.Semaphore(int(os.environ.get('GATEWAY_MAX_CONNECTIONS','512')));TIMEOUT=float(os.environ.get('GATEWAY_READ_TIMEOUT','15'));MAX_INITIAL=65536
HTTP=(b'GET ',b'POST ',b'HEAD ',b'PUT ',b'OPTIONS ',b'PATCH ',b'DELETE ',b'PRI * HTTP/2.0')
def subscription(token):
    if not TOKEN.exists() or token!=TOKEN.read_text().strip():return None,'TOKEN_INVALID'
    lines=[x.strip() for x in SUB.read_text().splitlines() if x.strip()]
    if len(lines)!=3 or any(not x.startswith('vless://') for x in lines):return None,'SUB_INVALID'
    return base64.b64encode('\n'.join(lines).encode()),'OK'
def tls_sni(buf):
    if len(buf)<5 or buf[0]!=0x16 or buf[1]!=0x03:return None
    rl=struct.unpack('!H',buf[3:5])[0]
    if len(buf)<5+rl:return None
    p=5
    if p+4>len(buf) or buf[p]!=1:return None
    hl=int.from_bytes(buf[p+1:p+4],'big');p+=4;end=min(len(buf),p+hl)
    if p+34>end:return None
    p+=34
    if p+1>end:return None
    n=buf[p];p+=1+n
    if p+2>end:return None
    n=struct.unpack('!H',buf[p:p+2])[0];p+=2+n
    if p+1>end:return None
    n=buf[p];p+=1+n
    if p+2>end:return None
    n=struct.unpack('!H',buf[p:p+2])[0];p+=2;ee=min(end,p+n)
    while p+4<=ee:
        typ,ln=struct.unpack('!HH',buf[p:p+4]);p+=4
        if p+ln>ee:break
        if typ==0 and ln>=5:
            q=p+2;stop=p+ln
            while q+3<=stop:
                nt=buf[q];nl=struct.unpack('!H',buf[q+1:q+3])[0];q+=3
                if q+nl>stop:break
                if nt==0:
                    try:return buf[q:q+nl].decode('idna')
                    except Exception:return buf[q:q+nl].decode('ascii','ignore')
                q+=nl
        p+=ln
    return None
async def read_initial(reader):
    buf=bytearray();deadline=asyncio.get_running_loop().time()+TIMEOUT
    while len(buf)<MAX_INITIAL:
        left=max(0.05,deadline-asyncio.get_running_loop().time())
        try:chunk=await asyncio.wait_for(reader.read(min(8192,MAX_INITIAL-len(buf))),left)
        except asyncio.TimeoutError:break
        if not chunk:break
        buf.extend(chunk);b=bytes(buf)
        if b.startswith(HTTP):
            if b'\r\n\r\n' in b or len(b)>8192:return b
        elif tls_sni(b) is not None:return b
        elif len(b)>=5 and b[0]==0x16 and b[1]==0x03 and len(b)>=5+struct.unpack('!H',b[3:5])[0]:return b
        elif len(b)>=1 and b[0]!=0x16:return b
    return bytes(buf)
async def pipe(r,w):
    try:
        while True:
            b=await r.read(65536)
            if not b:return
            w.write(b);await w.drain()
    except (ConnectionError,asyncio.CancelledError):pass
async def relay(reader,writer,initial,dest,label,sni='-'):
    up=None;tasks=set()
    try:
        ur,up=await asyncio.open_connection(*dest);up.write(initial);await up.drain();print(f'[gateway] ROUTE={label} sni={sni}',flush=True)
        tasks={asyncio.create_task(pipe(reader,up)),asyncio.create_task(pipe(ur,writer))};done,pending=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            try:t.result()
            except Exception:pass
        for t in pending:t.cancel()
        await asyncio.gather(*pending,return_exceptions=True)
    except Exception as e:print(f'[gateway] RELAY_ERROR={label}:{type(e).__name__}:{e}',flush=True)
    finally:
        for t in tasks:
            if not t.done():t.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        for w in (writer,up):
            if w:
                try:w.close();await w.wait_closed()
                except Exception:pass
async def http(reader,writer,initial):
    first=initial.split(b'\r\n',1)[0].decode('latin1','ignore');parts=first.split(' ',2);method=parts[0] if parts else '';target=parts[1] if len(parts)>1 else '';path=urllib.parse.urlsplit(target).path
    if method in ('GET','HEAD') and path=='/ready':
        body=b'ready\n';out=b'' if method=='HEAD' else body;writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: '+str(len(body)).encode()+b'\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n'+out);await writer.drain();return
    m=re.fullmatch(r'/sub/([A-Za-z0-9_-]{20,128})/?',path)
    if method in ('GET','HEAD') and m:
        payload,status=subscription(urllib.parse.unquote(m.group(1)))
        if payload is not None:
            body=b'' if method=='HEAD' else payload;resp=b'HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: base64\r\nCache-Control: no-store\r\nConnection: close\r\nContent-Length: '+str(len(payload)).encode()+b'\r\n\r\n'+body
        else:
            body=(status+'\n').encode();code=b'404 Not Found' if status=='TOKEN_INVALID' else b'500 Internal Server Error';resp=b'HTTP/1.1 '+code+b'\r\nContent-Type: text/plain\r\nConnection: close\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body
        writer.write(resp);await writer.drain();return
    if method in ('GET','HEAD') and path in ('/','/index.html'):
        body=SITE.read_bytes();out=b'' if method=='HEAD' else body;writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: '+str(len(body)).encode()+b'\r\nConnection: close\r\n\r\n'+out);await writer.drain();return
    await relay(reader,writer,initial,HTTP_DEST,'http-xhttp','-')
async def handle(reader,writer):
    async with SEM:
        try:
            initial=await read_initial(reader)
            if not initial:return
            if initial.startswith(HTTP):await http(reader,writer,initial);return
            sni=tls_sni(initial);route=ROUTES.get(sni)
            if route:
                await relay(reader,writer,initial,(route[0],route[1]),route[2],sni);return
            print(f'[gateway] ROUTE_REJECT unknown_sni={sni or "-"}',flush=True)
        except Exception as e:print(f'[gateway] ERROR={type(e).__name__}:{e}',flush=True)
        finally:
            try:writer.close();await writer.wait_closed()
            except Exception:pass
async def main():
    server=await asyncio.start_server(handle,'0.0.0.0',PORT,limit=65536);print(f'GATEWAY_READY={PORT}',flush=True);print('[gateway] ROUTES='+','.join(f'{k}->{v[1]}' for k,v in ROUTES.items()),flush=True)
    try:await server.serve_forever()
    finally:server.close();await server.wait_closed()
if __name__=='__main__':asyncio.run(main())
