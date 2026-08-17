#!/usr/bin/env python3
import asyncio, base64, os, re, urllib.parse
from pathlib import Path

PORTS=tuple(int(x) for x in os.environ.get("GATEWAY_PORTS","8080,8081,8082,8083").split(",") if x)
D=Path(os.environ.get("DATA_DIR","/data")); SITE=Path("/opt/xray/site/index.html")
DEST={8080:("127.0.0.1",10086),8081:("127.0.0.1",10087),8082:("127.0.0.1",10088),8083:("127.0.0.1",10089)}
TOKEN=D/"subscription_token.txt"; SUB=D/"subscription.txt"
SEM=asyncio.Semaphore(int(os.environ.get("GATEWAY_MAX_CONNECTIONS","512"))); TIMEOUT=float(os.environ.get("GATEWAY_READ_TIMEOUT","15"))
HTTP=(b"GET ",b"POST ",b"HEAD ",b"PUT ",b"OPTIONS ",b"PATCH ",b"DELETE ",b"PRI * HTTP/2.0")

def subscription(token):
    if not TOKEN.exists() or token != TOKEN.read_text().strip(): return None,"TOKEN_INVALID"
    lines=[x.strip() for x in SUB.read_text().splitlines() if x.strip()]
    if len(lines)!=4 or any(not x.startswith("vless://") for x in lines): return None,"SUB_INVALID"
    return base64.b64encode("\n".join(lines).encode()),"OK"

async def pipe(r,w):
    try:
        while True:
            b=await r.read(65536)
            if not b:return
            w.write(b);await w.drain()
    except (ConnectionError,asyncio.CancelledError): pass

async def relay(reader,writer,initial,dest,label):
    up=None;tasks=set()
    try:
        ur,up=await asyncio.open_connection(*dest);up.write(initial);await up.drain()
        print(f"[gateway] ROUTE={label} port={writer.get_extra_info('sockname')[1]} target={dest[0]}:{dest[1]}",flush=True)
        tasks={asyncio.create_task(pipe(reader,up)),asyncio.create_task(pipe(ur,writer))}
        done,pending=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            try:t.result()
            except Exception:pass
        for t in pending:t.cancel()
        await asyncio.gather(*pending,return_exceptions=True)
    except Exception as e: print(f"[gateway] RELAY_ERROR={label}:{type(e).__name__}:{e}",flush=True)
    finally:
        for t in tasks:
            if not t.done():t.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        for w in (writer,up):
            if w:
                try:w.close();await w.wait_closed()
                except Exception:pass

async def http(reader,writer,initial):
    first=initial.split(b"\r\n",1)[0].decode("latin1","ignore");parts=first.split(" ",2)
    method=parts[0] if parts else "";target=parts[1] if len(parts)>1 else "";path=urllib.parse.urlsplit(target).path
    if method in ("GET","HEAD") and path=="/ready":
        body=b"ready\n";out=b"" if method=="HEAD" else body
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: "+str(len(body)).encode()+b"\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n"+out);await writer.drain()
        print(f"[gateway] HEALTHCHECK=/ready 200 port={writer.get_extra_info('sockname')[1]}",flush=True);return
    m=re.fullmatch(r"/sub/([A-Za-z0-9_-]{20,128})/?",path)
    if method in ("GET","HEAD") and m:
        payload,status=subscription(urllib.parse.unquote(m.group(1)))
        if payload is not None:
            body=b"" if method=="HEAD" else payload
            resp=b"HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: base64\r\nCache-Control: no-store\r\nConnection: close\r\nContent-Length: "+str(len(payload)).encode()+b"\r\n\r\n"+body
        else:
            body=(status+"\n").encode();code=b"404 Not Found" if status=="TOKEN_INVALID" else b"500 Internal Server Error"
            resp=b"HTTP/1.1 "+code+b"\r\nContent-Type: text/plain\r\nConnection: close\r\nContent-Length: "+str(len(body)).encode()+b"\r\n\r\n"+body
        writer.write(resp);await writer.drain();return
    if method in ("GET","HEAD") and path in ("/","/index.html"):
        body=SITE.read_bytes();out=b"" if method=="HEAD" else body
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: "+str(len(body)).encode()+b"\r\nConnection: close\r\n\r\n"+out);await writer.drain();return
    await relay(reader,writer,initial,DEST[8080],"http-xhttp")

async def handle(reader,writer):
    port=writer.get_extra_info("sockname")[1]
    async with SEM:
        try:
            initial=await asyncio.wait_for(reader.read(65536),TIMEOUT)
            if not initial:return
            # Railway may run /ready against the service target port, which can be
            # different from 8080 when TCP proxy application port is configured.
            # Only /ready is accepted as HTTP on every gateway port; all real proxy
            # traffic keeps its original physical route.
            first_line=initial.split(b"\r\n",1)[0]
            is_ready_http=initial.startswith(HTTP) and b" /ready" in first_line
            if is_ready_http:
                await http(reader,writer,initial)
            elif port==8080:
                await http(reader,writer,initial) if initial.startswith(HTTP) else await relay(reader,writer,initial,DEST[8080],"8080-xhttp")
            elif port==8081: await relay(reader,writer,initial,DEST[8081],"8081-raw-reality")
            elif port==8082: await relay(reader,writer,initial,DEST[8082],"8082-grpc-reality")
            elif port==8083: await relay(reader,writer,initial,DEST[8083],"8083-ws-tls")
        except Exception as e: print(f"[gateway] ERROR port={port}:{type(e).__name__}:{e}",flush=True)
        finally:
            try:writer.close();await writer.wait_closed()
            except Exception:pass

async def main():
    servers=[await asyncio.start_server(handle,"0.0.0.0",p,limit=65536) for p in PORTS]
    print("GATEWAY_READY=8080->10086 8081->10087 8082->10088 8083->10089",flush=True)
    try:await asyncio.gather(*(s.serve_forever() for s in servers))
    finally:
        for s in servers:s.close();await s.wait_closed()

if __name__=="__main__":asyncio.run(main())
