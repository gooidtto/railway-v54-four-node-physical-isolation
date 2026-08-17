#!/usr/bin/env python3
import asyncio
import base64
import os
import re
from pathlib import Path

PORTS = tuple(int(x.strip()) for x in os.environ.get("GATEWAY_PORTS", "8080,8081").split(",") if x.strip())
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
    except Exception as exc:
        print(f"[gateway] pipe error={type(exc).__name__}: {exc}", flush=True)


async def relay_to(reader, writer, initial, dest, label):
    upstream = None
    tasks = set()
    try:
        up_r, upstream = await asyncio.open_connection(*dest)
        upstream.write(initial)
        await upstream.drain()
        local_port = writer.get_extra_info("sockname")[1]
        peer = writer.get_extra_info("peername")
        print(f"[gateway] ROUTE={label} port={local_port} peer={peer} target={dest[0]}:{dest[1]}", flush=True)

        tasks = {
            asyncio.create_task(pipe(reader, upstream)),
            asyncio.create_task(pipe(up_r, writer)),
        }
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            try:
                task.result()
            except (ConnectionError, asyncio.CancelledError):
                pass
            except Exception as exc:
                print(f"[gateway] relay={label} task error={type(exc).__name__}: {exc}", flush=True)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    except (ConnectionError, asyncio.TimeoutError) as exc:
        print(f"[gateway] relay={label} connection={type(exc).__name__}: {exc}", flush=True)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"[gateway] relay={label} error={type(exc).__name__}: {exc}", flush=True)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for w in (writer, upstream):
            if w:
                try:
                    w.close()
                except Exception:
                    pass
        if upstream:
            try:
                await upstream.wait_closed()
            except Exception:
                pass


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

            # Both Railway entry ports terminate at the same protocol-aware
            # gateway. HTTP is forwarded to XHTTP; TLS ClientHello is forwarded
            # byte-for-byte to the private REALITY listener.
            if is_tls_client_hello(initial):
                await relay_to(reader, writer, initial, REALITY_DEST, "tls-reality")
                return

            if initial.startswith(HTTP_PREFIXES):
                await handle_http(reader, writer, initial)
                return

            print(f"[gateway] REJECT peer={peer} unknown protocol", flush=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[gateway] ERROR peer={peer}: {type(exc).__name__}: {exc}", flush=True)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


async def main():
    servers = []
    for port in PORTS:
        servers.append(await asyncio.start_server(handle, "0.0.0.0", port, limit=65536))
    print(f"GATEWAY_READY=ports={','.join(map(str, PORTS))} HTTP->10086 TLS->10087", flush=True)
    try:
        await asyncio.gather(*(serve.serve_forever() for serve in servers))
    finally:
        for serve in servers:
            serve.close()
            await serve.wait_closed()
        await asyncio.gather(*(serve.wait_closed() for serve in servers), return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
