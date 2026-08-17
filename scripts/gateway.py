#!/usr/bin/env python3
import asyncio
import base64
import os
import re
from pathlib import Path

LISTEN = ("0.0.0.0", 8080)
HTTP_DEST = ("127.0.0.1", int(os.environ.get("XRAY_HTTP_PORT", "10086")))
REALITY_DEST = ("127.0.0.1", int(os.environ.get("XRAY_REALITY_PORT", "10087")))
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


def is_tls_client_hello(data):
    return len(data) >= 3 and data[0] == 0x16 and data[1] == 0x03 and data[2] in (0x01, 0x02, 0x03, 0x04)


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


async def relay_to(reader, writer, initial, dest, label):
    up = None
    try:
        up_r, up = await asyncio.open_connection(*dest)
        up.write(initial)
        await up.drain()
        print(f"[gateway] ROUTE={label} peer={writer.get_extra_info('peername')} target={dest[0]}:{dest[1]}", flush=True)
        await asyncio.gather(pipe(reader, up), pipe(up_r, writer))
    except Exception as e:
        print(f"[gateway] relay={label} error={type(e).__name__}: {e}", flush=True)
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
                b"HTTP/1.1 404 Not Found\r\nContent-Type: text/plain\r\n"
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

    await relay_to(reader, writer, initial, HTTP_DEST, "http-xhttp")


async def handle(reader, writer):
    peer = writer.get_extra_info("peername")
    async with SEM:
        try:
            initial = await asyncio.wait_for(reader.read(65536), TIMEOUT)
            if not initial:
                return

            # The single Railway TCP Proxy and the Generate Domain both enter
            # the same gateway. HTTP is sent to XHTTP; TLS ClientHello is sent
            # byte-for-byte to the REALITY listener. No TLS termination occurs
            # in the gateway.
            if is_tls_client_hello(initial):
                await relay_to(reader, writer, initial, REALITY_DEST, "tls-reality")
                return

            if initial.startswith(HTTP_PREFIXES):
                await handle_http(reader, writer, initial)
                return

            print(f"[gateway] REJECT peer={peer} unknown protocol", flush=True)
            writer.close()
        except Exception as e:
            print(f"[gateway] ERROR peer={peer}: {type(e).__name__}: {e}", flush=True)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


async def main():
    server = await asyncio.start_server(handle, *LISTEN, limit=65536)
    print("GATEWAY_READY=8080 HTTP->10086 TLS->10087", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
