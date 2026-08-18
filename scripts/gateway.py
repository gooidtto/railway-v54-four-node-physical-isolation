#!/usr/bin/env python3
import asyncio
import base64
import json
import logging
import os
import re
import socket
import struct
import urllib.parse
from pathlib import Path

PORT = int(os.environ.get("GATEWAY_PORT", "8080"))
D = Path(os.environ.get("DATA_DIR", "/data"))
SITE = Path("/opt/xray/site/index.html")
TOKEN = D / "subscription_token.txt"
SUB = D / "subscription.txt"
RUNTIME = D / "runtime.json"

HTTP_DEST = ("127.0.0.1", 10086)
ROUTES = {
    os.environ.get("REALITY_RAW_SNI", "www.cloudflare.com").strip() or "www.cloudflare.com":
        ("127.0.0.1", 10087, "raw-reality-vision"),
    os.environ.get("REALITY_XHTTP_SNI", "www.apple.com").strip() or "www.apple.com":
        ("127.0.0.1", 10088, "xhttp-reality"),
}

MAX_CONNECTIONS = max(16, int(os.environ.get("GATEWAY_MAX_CONNECTIONS", "512")))
INITIAL_TIMEOUT = max(2.0, float(os.environ.get("GATEWAY_READ_TIMEOUT", "15")))
UPSTREAM_TIMEOUT = max(2.0, float(os.environ.get("GATEWAY_UPSTREAM_TIMEOUT", "10")))
IDLE_TIMEOUT = max(30.0, float(os.environ.get("GATEWAY_IDLE_TIMEOUT", "900")))
MAX_INITIAL = min(262144, max(4096, int(os.environ.get("GATEWAY_MAX_INITIAL", "65536"))))
SEM = asyncio.Semaphore(MAX_CONNECTIONS)
HTTP = (b"GET ", b"POST ", b"HEAD ", b"PUT ", b"OPTIONS ", b"PATCH ", b"DELETE ", b"PRI * HTTP/2.0")

logging.basicConfig(
    level=getattr(logging, os.environ.get("GATEWAY_LOGLEVEL", "WARNING").upper(), logging.WARNING),
    format="[gateway] %(levelname)s %(message)s",
)
log = logging.getLogger("gateway")


def expected_nodes():
    try:
        state = json.loads(RUNTIME.read_text())
        n = int(state.get("nodes", {}).get("count", 0))
        return n if n in (3, 4) else 0
    except Exception:
        return 0


def local_port_ready(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.5):
            return True
    except OSError:
        return False


def cloudflare_ready():
    try:
        state = json.loads(RUNTIME.read_text())
        cf = state.get("cloudflare", {})
        if not cf.get("enabled"):
            return True
        import urllib.request
        urllib.request.urlopen("http://127.0.0.1:2000/ready", timeout=2).read()
        return True
    except Exception:
        return False


def readiness():
    expected = expected_nodes()
    if expected not in (3, 4):
        return False, "runtime"
    if not RUNTIME.exists() or not SUB.exists() or not TOKEN.exists():
        return False, "state"
    lines = [x.strip() for x in SUB.read_text().splitlines() if x.strip()]
    if len(lines) != expected or any(not x.startswith("vless://") for x in lines):
        return False, "subscription"
    for port, label in ((10086, "xhttp-http"), (10087, "raw-reality"), (10088, "xhttp-reality")):
        if not local_port_ready(port):
            return False, label
    if not cloudflare_ready():
        return False, "cloudflare"
    return True, "ready"


def subscription(token):
    if not TOKEN.exists() or token != TOKEN.read_text().strip():
        return None, "TOKEN_INVALID"
    if not SUB.exists():
        return None, "SUB_MISSING"
    lines = [x.strip() for x in SUB.read_text().splitlines() if x.strip()]
    expected = expected_nodes()
    if expected not in (3, 4):
        return None, "RUNTIME_INVALID"
    if len(lines) != expected or any(not x.startswith("vless://") for x in lines):
        return None, "SUB_INVALID"
    return base64.b64encode("\n".join(lines).encode()), "OK"


def tls_sni(buf):
    if len(buf) < 5 or buf[0] != 0x16 or buf[1] != 0x03:
        return None
    rl = struct.unpack("!H", buf[3:5])[0]
    if len(buf) < 5 + rl:
        return None
    p = 5
    if p + 4 > len(buf) or buf[p] != 1:
        return None
    hl = int.from_bytes(buf[p + 1:p + 4], "big")
    p += 4
    end = min(len(buf), p + hl)
    if p + 34 > end:
        return None
    p += 34
    if p + 1 > end:
        return None
    n = buf[p]
    p += 1 + n
    if p + 2 > end:
        return None
    n = struct.unpack("!H", buf[p:p + 2])[0]
    p += 2 + n
    if p + 1 > end:
        return None
    n = buf[p]
    p += 1 + n
    if p + 2 > end:
        return None
    n = struct.unpack("!H", buf[p:p + 2])[0]
    p += 2
    ee = min(end, p + n)
    while p + 4 <= ee:
        typ, ln = struct.unpack("!HH", buf[p:p + 4])
        p += 4
        if p + ln > ee:
            break
        if typ == 0 and ln >= 5:
            q = p + 2
            stop = p + ln
            while q + 3 <= stop:
                nt = buf[q]
                nl = struct.unpack("!H", buf[q + 1:q + 3])[0]
                q += 3
                if q + nl > stop:
                    break
                if nt == 0:
                    try:
                        return buf[q:q + nl].decode("idna")
                    except Exception:
                        return buf[q:q + nl].decode("ascii", "ignore")
                q += nl
        p += ln
    return None


async def read_initial(reader):
    buf = bytearray()
    deadline = asyncio.get_running_loop().time() + INITIAL_TIMEOUT
    while len(buf) < MAX_INITIAL:
        left = max(0.05, deadline - asyncio.get_running_loop().time())
        try:
            chunk = await asyncio.wait_for(reader.read(min(8192, MAX_INITIAL - len(buf))), left)
        except asyncio.TimeoutError:
            break
        if not chunk:
            break
        buf.extend(chunk)
        b = bytes(buf)
        if b.startswith(HTTP):
            if b"\r\n\r\n" in b or len(b) > 8192:
                return b
        elif tls_sni(b) is not None:
            return b
        elif len(b) >= 5 and b[0] == 0x16 and b[1] == 0x03 and len(b) >= 5 + struct.unpack("!H", b[3:5])[0]:
            return b
        elif len(b) >= 1 and b[0] != 0x16:
            return b
    return bytes(buf)


async def pipe(r, w):
    try:
        while True:
            b = await asyncio.wait_for(r.read(65536), timeout=IDLE_TIMEOUT)
            if not b:
                return
            w.write(b)
            await w.drain()
    except (asyncio.TimeoutError, ConnectionError, asyncio.IncompleteReadError):
        return
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.debug("pipe error: %s", exc)


async def relay(reader, writer, initial, dest, label, sni="-"):
    up = None
    tasks = set()
    try:
        ur, up = await asyncio.wait_for(asyncio.open_connection(*dest), timeout=UPSTREAM_TIMEOUT)
        up.write(initial)
        await up.drain()
        log.info("ROUTE=%s sni=%s", label, sni)
        tasks = {
            asyncio.create_task(pipe(reader, up)),
            asyncio.create_task(pipe(ur, writer)),
        }
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            try:
                task.result()
            except Exception:
                pass
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    except asyncio.TimeoutError:
        log.warning("UPSTREAM_TIMEOUT route=%s", label)
    except Exception as exc:
        log.warning("RELAY_ERROR route=%s error=%s:%s", label, type(exc).__name__, exc)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for sock in (writer, up):
            if sock:
                try:
                    sock.close()
                    await sock.wait_closed()
                except Exception:
                    pass


async def write_response(writer, status, body=b"", content_type=b"text/plain; charset=utf-8"):
    response = (
        b"HTTP/1.1 " + status + b"\r\n"
        b"Content-Type: " + content_type + b"\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"Cache-Control: no-store\r\n"
        b"Connection: close\r\n\r\n" + body
    )
    writer.write(response)
    await writer.drain()


async def http(reader, writer, initial):
    first = initial.split(b"\r\n", 1)[0].decode("latin1", "ignore")
    parts = first.split(" ", 2)
    method = parts[0] if parts else ""
    target = parts[1] if len(parts) > 1 else ""
    path = urllib.parse.urlsplit(target).path

    if method in ("GET", "HEAD") and path in ("/health", "/ready"):
        if path == "/health":
            body = b"healthy\n"
            status = b"200 OK"
        else:
            ok, reason = readiness()
            body = ("ready\n" if ok else "not-ready:" + reason + "\n").encode()
            status = b"200 OK" if ok else b"503 Service Unavailable"
        if method == "HEAD":
            body = b""
        await write_response(writer, status, body)
        return

    m = re.fullmatch(r"/sub/([A-Za-z0-9_-]{20,128})/?", path)
    if method in ("GET", "HEAD") and m:
        payload, status = subscription(urllib.parse.unquote(m.group(1)))
        if payload is not None:
            body = b"" if method == "HEAD" else payload
            await write_response(writer, b"200 OK", body, b"text/plain; charset=utf-8")
        else:
            body = (status + "\n").encode()
            code = b"404 Not Found" if status == "TOKEN_INVALID" else b"500 Internal Server Error"
            await write_response(writer, code, body)
        return

    if method in ("GET", "HEAD") and path in ("/", "/index.html"):
        body = SITE.read_bytes()
        if method == "HEAD":
            body = b""
        await write_response(writer, b"200 OK", body, b"text/html; charset=utf-8")
        return

    await relay(reader, writer, initial, HTTP_DEST, "http-xhttp", "-")


async def handle(reader, writer):
    async with SEM:
        try:
            initial = await read_initial(reader)
            if not initial:
                return
            if initial.startswith(HTTP):
                await http(reader, writer, initial)
                return
            sni = tls_sni(initial)
            route = ROUTES.get(sni)
            if route:
                await relay(reader, writer, initial, (route[0], route[1]), route[2], sni)
                return
            log.warning("ROUTE_REJECT unknown_sni=%s", sni or "-")
        except Exception as exc:
            log.warning("ERROR=%s:%s", type(exc).__name__, exc)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


async def main():
    server = await asyncio.start_server(handle, "0.0.0.0", PORT, limit=65536)
    log.warning("GATEWAY_READY=%s max_connections=%s idle_timeout=%ss", PORT, MAX_CONNECTIONS, IDLE_TIMEOUT)
    log.warning("ROUTES=%s", ",".join(f"{k}->{v[1]}" for k, v in ROUTES.items()))
    try:
        await server.serve_forever()
    finally:
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
