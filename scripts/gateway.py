#!/usr/bin/env python3
import asyncio, base64, os, re
from pathlib import Path

LISTEN = ("0.0.0.0", 8080)
DEST = ("127.0.0.1", int(os.environ.get("XRAY_HTTP_PORT", "10086")))
DATA = Path(os.environ.get("DATA_DIR", "/data"))
SITE = Path("/opt/xray/site/index.html")
TOKEN = DATA / "subscription_token.txt"
SUB = DATA / "subscription.txt"
MAX_CONN = int(os.environ.get("GATEWAY_MAX_CONNECTIONS", "512"))
TIMEOUT = float(os.environ.get("GATEWAY_READ_TIMEOUT", "15"))
SEM = asyncio.Semaphore(MAX_CONN)
HTTP_PREFIXES = (
    b"GET ", b"POST ", b"HEAD ", b"PUT ", b"OPTIONS ",
    b"PATCH ", b"DELETE ", b"PRI * HTTP/2.0"
)

def subscription(token):
    try:
        if token != TOKEN.read_text().strip():
            return None
        raw = SUB.read_bytes()
        return base64.b64encode(raw) if raw.strip() else None
    except OSError:
        return None

async def pipe(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                return
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        return

async def relay(reader, writer, initial):
    up = None
    try:
        up_r, up = await asyncio.open_connection(*DEST)
        up.write(initial)
        await up.drain()
        await asyncio.gather(pipe(reader, up), pipe(up_r, writer))
    except Exception as e:
        print(f"relay error: {type(e).__name__}: {e}", flush=True)
    finally:
        for w in (writer, up):
            if w:
                w.close()

async def handle_http(reader, writer, initial):
    line = initial.split(b"\r\n", 1)[0].decode("latin1", "ignore")
    m = re.match(r"^(?:GET|HEAD) /sub/([A-Za-z0-9_-]{20,128})(?:\?[^ ]*)? HTTP/", line)
    if m:
        payload = subscription(m.group(1))
        if payload is None:
            body = b"not found\n"
            resp = (
                b"HTTP/1.1 404 Not Found\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Length: " + str(len(body)).encode() +
                b"\r\nConnection: close\r\n\r\n" + body
            )
        elif line.startswith("HEAD"):
            resp = (
                b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                b"Content-Length: " + str(len(payload)).encode() +
                b"\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n"
            )
        else:
            resp = (
                b"HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\n"
                b"Content-Length: " + str(len(payload)).encode() +
                b"\r\nCache-Control: no-store\r\nContent-Disposition: inline\r\n"
                b"Connection: close\r\n\r\n" + payload
            )
        writer.write(resp)
        await writer.drain()
        return

    if line.startswith(("GET /ready", "HEAD /ready")):
        body = b"ready\n"
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
            b"Content-Length: 6\r\nConnection: close\r\n\r\n" + body
        )
        await writer.drain()
        return

    if line.startswith(("GET / ", "GET /index.html", "HEAD / ", "HEAD /index.html")):
        body = SITE.read_bytes()
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
            b"Content-Length: " + str(len(body)).encode() +
            b"\r\nConnection: close\r\n\r\n" + body
        )
        await writer.drain()
        return

    await relay(reader, writer, initial)

async def handle(reader, writer):
    peer = writer.get_extra_info("peername")
    async with SEM:
        try:
            initial = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"), TIMEOUT
            )
            if not initial.startswith(HTTP_PREFIXES):
                print(f"gateway rejected peer={peer}", flush=True)
                writer.close()
                return
            await handle_http(reader, writer, initial)
        except Exception as e:
            print(f"gateway error peer={peer}: {type(e).__name__}: {e}", flush=True)
        finally:
            writer.close()

async def main():
    server = await asyncio.start_server(handle, *LISTEN, limit=65536)
    print("HTTP_GATEWAY_READY=8080 -> 127.0.0.1:10086", flush=True)
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
