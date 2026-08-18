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
    os.environ.get("REALITY_RAW_SNI", "www.cloudflare.com").strip().lower().rstrip(".") or "www.cloudflare.com":
        ("127.0.0.1", 10087, "raw-reality-vision"),
    os.environ.get("REALITY_XHTTP_SNI", "www.apple.com").strip().lower().rstrip(".") or "www.apple.com":
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
    level=getattr(logging, os.environ.get("GATEWAY_LOGLEVEL", "INFO").upper(), logging.INFO),
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


def _tls_client_hello(buf):
    if len(buf) < 5 or buf[0] != 0x16 or buf[1] != 0x03:
        return False, None
    pos = 0
    handshake = bytearray()
    saw_record = False
    while pos + 5 <= len(buf):
        content_type = buf[pos]
        major = buf[pos + 1]
        record_len = struct.unpack("!H", buf[pos + 3:pos + 5])[0]
        if major != 3 or content_type not in (20, 21, 22, 23):
            return False, None
        if pos + 5 + record_len > len(buf):
            return False, None
        saw_record = True
        payload = buf[pos + 5:pos + 5 + record_len]
        if content_type == 22:
            handshake.extend(payload)
            while len(handshake) >= 4:
                hs_type = handshake[0]
                hs_len = int.from_bytes(handshake[1:4], "big")
                total = 4 + hs_len
                if len(handshake) < total:
                    break
                if hs_type == 1:
                    return True, _parse_client_hello_sni(bytes(handshake[:total]))
                del handshake[:total]
        pos += 5 + record_len
    return False, None


def _parse_client_hello_sni(handshake):
    if len(handshake) < 4 or handshake[0] != 1:
        return None
    hs_len = int.from_bytes(handshake[1:4], "big")
    end = min(len(handshake), 4 + hs_len)
    p = 4
    if p + 34 > end:
        return None
    p += 34
    if p + 1 > end:
        return None
    session_len = handshake[p]
    p += 1 + session_len
    if p + 2 > end:
        return None
    cipher_len = struct.unpack("!H", handshake[p:p + 2])[0]
    p += 2 + cipher_len
    if p + 1 > end:
        return None
    compression_len = handshake[p]
    p += 1 + compression_len
    if p + 2 > end:
        return None
    extensions_len = struct.unpack("!H", handshake[p:p + 2])[0]
    p += 2
    ext_end = min(end, p + extensions_len)
    while p + 4 <= ext_end:
        typ, ln = struct.unpack("!HH", handshake[p:p + 4])
        p += 4
        if p + ln > ext_end:
            return None
        if typ == 0 and ln >= 5:
            q = p + 2
            stop = p + ln
            while q + 3 <= stop:
                name_type = handshake[q]
                name_len = struct.unpack("!H", handshake[q + 1:q + 3])[0]
                q += 3
                if q + name_len > stop:
                    return None
                if name_type == 0:
                    try:
                        return handshake[q:q + name_len].decode("idna").strip().lower().rstrip(".")
                    except Exception:
                        return handshake[q:q + name_len].decode("ascii", "ignore").strip().lower().rstrip(".")
                q += name_len
        p += ln
    return None


def tls_sni(buf):
    return _tls_client_hello(buf)[1]


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
        elif b[:1] == b"\x16" and len(b) >= 3 and b[1] == 0x03:
            complete, _ = _tls_client_hello(b)
            if complete:
                return b
        elif b[:1] != b"\x16":
            return b
    return bytes(buf)


async def pipe(r, w, direction):
    try:
        while True:
            b = await asyncio.wait_for(r.read(65536), timeout=IDLE_TIMEOUT)
            if not b:
                log.info("RELAY_EOF direction=%s", direction)
                return
            w.write(b)
            await w.drain()
    except asyncio.TimeoutError:
        log.info("RELAY_IDLE_TIMEOUT direction=%s", direction)
    except (ConnectionError, asyncio.IncompleteReadError) as exc:
        log.info("RELAY_CLOSE direction=%s error=%s", direction, type(exc).__name__)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.info("RELAY_ERROR direction=%s error=%s:%s", direction, type(exc).__name__, exc)


async def relay(reader, writer, initial, dest, label, sni="-"):
    up = None
    tasks = set()
    try:
        log.info("ROUTE_SELECTED route=%s sni=%s dest=%s:%s initial=%d", label, sni, dest[0], dest[1], len(initial))
        ur, up = await asyncio.wait_for(asyncio.open_connection(*dest), timeout=UPSTREAM_TIMEOUT)
        log.info("UPSTREAM_CONNECT_OK route=%s dest=%s:%s", label, dest[0], dest[1])
        try:
            up.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        try:
            writer.transport.get_extra_info("socket").setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass
        if initial:
            up.write(initial)
            await up.drain()
            log.info("INITIAL_FORWARDED route=%s bytes=%d", label, len(initial))
        tasks = {
            asyncio.create_task(pipe(reader, up, "client->upstream")),
            asyncio.create_task(pipe(ur, writer, "upstream->client")),
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
        log.warning("UPSTREAM_TIMEOUT route=%s dest=%s:%s", label, dest[0], dest[1])
    except Exception as exc:
        log.warning("RELAY_ERROR route=%s dest=%s:%s error=%s:%s", label, dest[0], dest[1], type(exc).__name__, exc)
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
    peer = writer.get_extra_info("peername")
    async with SEM:
        try:
            log.info("TCP_ACCEPT peer=%s", peer)
            initial = await read_initial(reader)
            if not initial:
                log.info("TCP_EMPTY peer=%s", peer)
                return
            log.info("TCP_INITIAL peer=%s bytes=%d first=%s", peer, len(initial), initial[:5].hex())
            if initial.startswith(HTTP):
                await http(reader, writer, initial)
                return

            tls = initial[:1] == b"\x16" and len(initial) >= 3 and initial[1] == 0x03
            if tls:
                sni = tls_sni(initial)
                log.info("TLS_SNI peer=%s sni=%s", peer, sni or "-")
                route = ROUTES.get(sni or "")
                if route:
                    await relay(reader, writer, initial, (route[0], route[1]), route[2], sni)
                    return
                log.warning("ROUTE_REJECT tls_sni=%s peer=%s", sni or "-", peer)
                return

            log.warning("ROUTE_REJECT unknown_protocol=0x%s peer=%s", initial[:1].hex() if initial else "-", peer)
        except Exception as exc:
            log.warning("ERROR peer=%s error=%s:%s", peer, type(exc).__name__, exc)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


async def main():
    server = await asyncio.start_server(handle, "0.0.0.0", PORT, limit=262144)
    log.warning("GATEWAY_READY=%s max_connections=%s idle_timeout=%ss", PORT, MAX_CONNECTIONS, IDLE_TIMEOUT)
    log.warning("ROUTES=%s", ",".join(f"{k}->{v[1]}" for k, v in ROUTES.items()))
    try:
        await server.serve_forever()
    finally:
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
