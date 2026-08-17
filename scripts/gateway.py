#!/usr/bin/env python3
import asyncio
import base64
import os
import re
import urllib.parse
from pathlib import Path

PORTS = tuple(int(x.strip()) for x in os.environ.get("GATEWAY_PORTS", "8080,8081,8082,8083").split(",") if x.strip())
HTTP_DEST = ("127.0.0.1", int(os.environ.get("XRAY_HTTP_PORT", "10086")))
REALITY_DEST = ("127.0.0.1", int(os.environ.get("XRAY_REALITY_PORT", "10087")))
DATA = Path(os.environ.get("DATA_DIR", "/data"))
SITE = Path("/opt/xray/site/index.html")
TOKEN = DATA / "subscription_token.txt"
SUB = DATA / "subscription.txt"
READY_FILE = Path(os.environ.get("GATEWAY_READY_FILE", str(DATA / "gateway.ready")))
MAX_CONN = int(os.environ.get("GATEWAY_MAX_CONNECTIONS", "512"))
TIMEOUT = float(os.environ.get("GATEWAY_READ_TIMEOUT", "15"))
SEM = asyncio.Semaphore(MAX_CONN)
HTTP_PREFIXES = (b"GET ", b"POST ", b"HEAD ", b"PUT ", b"OPTIONS ", b"PATCH ", b"DELETE ", b"PRI * HTTP/2.0")


def load_subscription(token, method):
    try:
        expected = TOKEN.read_text(encoding="utf-8").strip()
    except OSError as exc:
        print(f"[gateway] SUB_TOKEN_READ_ERROR={type(exc).__name__}:{exc}", flush=True)
        return None, "SUB_READ_ERROR"
    if not expected or token != expected:
        print(f"[gateway] SUB_TOKEN_INVALID expected_len={len(expected)} got_len={len(token)}", flush=True)
        return None, "TOKEN_INVALID"
    try:
        raw = SUB.read_bytes()
    except OSError as exc:
        print(f"[gateway] SUB_FILE_READ_ERROR={type(exc).__name__}:{exc}", flush=True)
        return None, "SUB_READ_ERROR"
    try:
        lines = [line.strip() for line in raw.decode("utf-8").splitlines() if line.strip()]
    except UnicodeDecodeError as exc:
        print(f"[gateway] SUB_UTF8_ERROR={exc}", flush=True)
        return None, "SUB_INVALID"
    if len(lines) != 8 or any(not line.startswith("vless://") for line in lines):
        print(f"[gateway] SUB_INVALID_LINES={len(lines)}", flush=True)
        return None, "SUB_INVALID"
    payload = base64.b64encode("\n".join(lines).encode("utf-8"))
    print(f"[gateway] SUB_REQUEST method={method} nodes={len(lines)} bytes={len(raw)} payload={len(payload)} response=200", flush=True)
    return payload, "OK"


def is_tls_client_hello(data):
    return len(data) >= 3 and data[0] == 0x16 and data[1] == 0x03 and data[2] in (0x01, 0x02, 0x03, 0x04)


def gateway_ready():
    return READY_FILE.is_file()


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
        tasks = {asyncio.create_task(pipe(reader, upstream)), asyncio.create_task(pipe(up_r, writer))}
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
    parts = line.split(" ", 2)
    method = parts[0] if parts else ""
    target = parts[1] if len(parts) > 1 else ""
    parsed = urllib.parse.urlsplit(target)
    path = parsed.path
    m = re.fullmatch(r"/sub/([A-Za-z0-9_-]{20,128})/?", path)
    if method in ("GET", "HEAD") and m:
        token = urllib.parse.unquote(m.group(1))
        try:
            payload, status = load_subscription(token, method)
        except Exception as exc:
            print(f"[gateway] SUB_UNEXPECTED_ERROR={type(exc).__name__}:{exc}", flush=True)
            payload, status = None, "SUB_READ_ERROR"
        if payload is not None:
            headers = (b"HTTP/1.1 200 OK\r\n" b"Content-Type: text/plain; charset=utf-8\r\n" b"Content-Transfer-Encoding: base64\r\n" b"Cache-Control: no-store, no-cache, must-revalidate\r\n" b"Pragma: no-cache\r\n" b"Content-Disposition: inline\r\n" b"Connection: close\r\n" b"Content-Length: " + str(len(payload)).encode() + b"\r\n\r\n")
            response = headers if method == "HEAD" else headers + payload
        else:
            body = (status + "\n").encode()
            code = b"404 Not Found" if status == "TOKEN_INVALID" else b"500 Internal Server Error"
            response = b"HTTP/1.1 " + code + b"\r\nContent-Type: text/plain; charset=utf-8\r\nCache-Control: no-store\r\nConnection: close\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        writer.write(response)
        await writer.drain()
        return

    if path == "/ready" and method in ("GET", "HEAD"):
        if gateway_ready():
            body = b"ready\n"
            response = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 6\r\nConnection: close\r\n\r\n" + (b"" if method == "HEAD" else body)
        else:
            body = b"starting\n"
            response = b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: text/plain\r\nContent-Length: 9\r\nRetry-After: 2\r\nConnection: close\r\n\r\n" + (b"" if method == "HEAD" else body)
        writer.write(response)
        await writer.drain()
        return

    if method in ("GET", "HEAD") and path in ("/", "/index.html"):
        body = SITE.read_bytes()
        response = b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + (b"" if method == "HEAD" else body)
        writer.write(response)
        await writer.drain()
        return
    await relay_to(reader, writer, initial, HTTP_DEST, "http-xhttp")


async def handle(reader, writer):
    peer = writer.get_extra_info("peername")
    local_port = writer.get_extra_info("sockname")[1]
    async with SEM:
        try:
            initial = await asyncio.wait_for(reader.read(65536), TIMEOUT)
            if not initial:
                return
            if is_tls_client_hello(initial):
                await relay_to(reader, writer, initial, REALITY_DEST, f"tls-reality-{local_port}")
                return
            if initial.startswith(HTTP_PREFIXES):
                await handle_http(reader, writer, initial)
                return
            print(f"[gateway] REJECT port={local_port} peer={peer} unknown protocol", flush=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[gateway] ERROR port={local_port} peer={peer}: {type(exc).__name__}: {exc}", flush=True)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


async def main():
    try:
        READY_FILE.unlink()
    except FileNotFoundError:
        pass
    servers = [await asyncio.start_server(handle, "0.0.0.0", port, limit=65536) for port in PORTS]
    print(f"GATEWAY_READY=ports={','.join(map(str, PORTS))} HTTP->10086 TLS->10087", flush=True)
    try:
        await asyncio.gather(*(serve.serve_forever() for serve in servers))
    finally:
        try:
            READY_FILE.unlink()
        except FileNotFoundError:
            pass
        for serve in servers:
            serve.close()
            await serve.wait_closed()
        await asyncio.gather(*(serve.wait_closed() for serve in servers), return_exceptions=True)

if __name__ == "__main__":
    asyncio.run(main())
