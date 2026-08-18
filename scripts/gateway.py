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
HTTP = (
    b"GET ", b"POST ", b"HEAD ", b"PUT ", b"OPTIONS ", b"PATCH ",
    b"DELETE ", b"PRI * HTTP/2.0"
)

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


def _parse_client_hello_sni(handshake):
    """Return SNI from one complete TLS ClientHello, or None if absent/invalid."""
    data = memoryview(handshake)
    if len(data) < 4 or data[0] != 1:
        return None, "not-client-hello"

    hs_len = int.from_bytes(data[1:4], "big")
    if hs_len != len(data) - 4:
        return None, "handshake-length"

    end = len(data)
    p = 4

    if p + 34 > end:
        return None, "legacy-version-random"
    p += 34

    if p + 1 > end:
        return None, "session-id-length"
    session_len = data[p]
    p += 1
    if p + session_len > end:
        return None, "session-id"
    p += session_len

    if p + 2 > end:
        return None, "cipher-suite-length"
    cipher_len = struct.unpack("!H", data[p:p + 2])[0]
    p += 2
    if cipher_len < 2 or cipher_len % 2 or p + cipher_len > end:
        return None, "cipher-suites"
    p += cipher_len

    if p + 1 > end:
        return None, "compression-length"
    compression_len = data[p]
    p += 1
    if p + compression_len > end:
        return None, "compression-methods"
    p += compression_len

    if p == end:
        return None, "no-extensions"
    if p + 2 > end:
        return None, "extensions-length"
    extensions_len = struct.unpack("!H", data[p:p + 2])[0]
    p += 2
    ext_end = p + extensions_len
    if ext_end != end:
        return None, "extensions-boundary"

    while p < ext_end:
        if p + 4 > ext_end:
            return None, "extension-header"
        ext_type, ext_len = struct.unpack("!HH", data[p:p + 4])
        p += 4
        if p + ext_len > ext_end:
            return None, "extension-length"

        if ext_type == 0:
            if ext_len < 2:
                return None, "sni-list-length"
            list_len = struct.unpack("!H", data[p:p + 2])[0]
            list_start = p + 2
            list_end = list_start + list_len
            if list_end != p + ext_len:
                return None, "sni-list-boundary"

            q = list_start
            while q < list_end:
                if q + 3 > list_end:
                    return None, "sni-entry-header"
                name_type = data[q]
                name_len = struct.unpack("!H", data[q + 1:q + 3])[0]
                q += 3
                if q + name_len > list_end:
                    return None, "sni-entry-length"
                if name_type == 0 and name_len > 0:
                    raw_name = bytes(data[q:q + name_len])
                    try:
                        name = raw_name.decode("ascii")
                    except UnicodeDecodeError:
                        return None, "sni-nonascii"
                    name = name.strip().lower().rstrip(".")
                    if not name or len(name) > 253:
                        return None, "sni-invalid"
                    if any(ord(c) < 0x21 or ord(c) > 0x7e for c in name):
                        return None, "sni-invalid-chars"
                    return name, "ok"
                q += name_len
            return None, "sni-hostname-missing"

        p += ext_len

    return None, "sni-extension-missing"


def _tls_client_hello(buf):
    """Parse a TLS ClientHello from an accumulated TCP stream.

    Returns (state, sni, reason, handshake_bytes). State is one of:
    COMPLETE, INCOMPLETE, INVALID. Only COMPLETE is routable.
    """
    data = memoryview(buf)
    if len(data) < 5:
        return "INCOMPLETE", None, "record-header", b""
    if data[0] != 0x16 or data[1] != 0x03:
        return "INVALID", None, "not-tls-handshake", b""

    pos = 0
    handshake = bytearray()
    max_handshake = MAX_INITIAL

    while pos < len(data):
        if len(data) - pos < 5:
            return "INCOMPLETE", None, "record-header", bytes(handshake)

        content_type = data[pos]
        major = data[pos + 1]
        minor = data[pos + 2]
        record_len = struct.unpack("!H", data[pos + 3:pos + 5])[0]

        if major != 3 or content_type not in (20, 21, 22, 23):
            return "INVALID", None, "record-header-invalid", bytes(handshake)

        record_end = pos + 5 + record_len
        if record_end > len(data):
            return "INCOMPLETE", None, "record-body", bytes(handshake)

        if content_type == 22:
            handshake.extend(data[pos + 5:record_end])
            if len(handshake) > max_handshake:
                return "INVALID", None, "client-hello-too-large", bytes(handshake)

            if len(handshake) >= 4:
                hs_type = handshake[0]
                hs_len = int.from_bytes(handshake[1:4], "big")
                total = 4 + hs_len
                if hs_type != 1:
                    return "INVALID", None, f"first-handshake-type-{hs_type}", bytes(handshake)
                if total > max_handshake:
                    return "INVALID", None, "handshake-too-large", bytes(handshake)
                if len(handshake) < total:
                    pos = record_end
                    continue

                hello = bytes(handshake[:total])
                sni, reason = _parse_client_hello_sni(hello)
                if sni:
                    return "COMPLETE", sni, "ok", hello
                return "COMPLETE", None, reason, hello

        pos = record_end

    if handshake:
        return "INCOMPLETE", None, "client-hello-fragment", bytes(handshake)
    return "INCOMPLETE", None, "no-handshake-data", b""


def tls_sni(buf):
    state, sni, _reason, _hello = _tls_client_hello(buf)
    return sni if state == "COMPLETE" else None


async def read_initial(reader, peer):
    buf = bytearray()
    deadline = asyncio.get_running_loop().time() + INITIAL_TIMEOUT
    connection_started = asyncio.get_running_loop().time()
    last_state = None

    while len(buf) < MAX_INITIAL:
        left = max(0.05, deadline - asyncio.get_running_loop().time())
        try:
            chunk = await asyncio.wait_for(
                reader.read(min(8192, MAX_INITIAL - len(buf))), left
            )
        except asyncio.TimeoutError:
            log.warning(
                "TLS_READ_TIMEOUT peer=%s:%s bytes=%d elapsed=%.2fs",
                peer[0], peer[1], len(buf), asyncio.get_running_loop().time() - connection_started,
            )
            return bytes(buf)
        if not chunk:
            return bytes(buf)

        buf.extend(chunk)
        b = bytes(buf)

        if b.startswith(HTTP):
            if b"\r\n\r\n" in b or len(b) > 8192:
                log.info("HTTP_REQUEST_COMPLETE peer=%s:%s bytes=%d", peer[0], peer[1], len(b))
                return b
            continue

        if b[:1] == b"\x16" and len(b) >= 3 and b[1] == 0x03:
            state, sni, reason, hello = _tls_client_hello(b)
            if state != last_state or state == "COMPLETE":
                log.info(
                    "TLS_PARSE peer=%s:%s state=%s bytes=%d handshake=%d sni=%s reason=%s",
                    peer[0], peer[1], state, len(b), len(hello), sni or "-", reason,
                )
                last_state = state
            if state == "COMPLETE":
                return b
            if state == "INVALID":
                return b
            continue

        log.warning(
            "UNKNOWN_PROTOCOL peer=%s:%s first=0x%s bytes=%d",
            peer[0], peer[1], b[:8].hex(), len(b),
        )
        return b

    log.warning("INITIAL_BUFFER_LIMIT peer=%s:%s bytes=%d", peer[0], peer[1], len(buf))
    return bytes(buf)


async def pipe(r, w, label):
    try:
        while True:
            b = await asyncio.wait_for(r.read(65536), timeout=IDLE_TIMEOUT)
            if not b:
                return
            w.write(b)
            await w.drain()
    except asyncio.TimeoutError:
        log.info("PIPE_IDLE_TIMEOUT label=%s", label)
    except (ConnectionError, asyncio.IncompleteReadError) as exc:
        log.info("PIPE_CLOSED label=%s error=%s", label, type(exc).__name__)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning("PIPE_ERROR label=%s error=%s:%s", label, type(exc).__name__, exc)


async def relay(reader, writer, initial, dest, label, sni="-"):
    up = None
    tasks = set()
    try:
        log.info(
            "UPSTREAM_CONNECT_START route=%s sni=%s dest=%s:%s initial=%d",
            label, sni, dest[0], dest[1], len(initial),
        )
        ur, up = await asyncio.wait_for(asyncio.open_connection(*dest), timeout=UPSTREAM_TIMEOUT)
        log.info("UPSTREAM_CONNECT_OK route=%s sni=%s dest=%s:%s", label, sni, dest[0], dest[1])
        up.write(initial)
        await up.drain()
        log.info("ROUTE=%s sni=%s dest=%s:%s", label, sni, dest[0], dest[1])

        tasks = {
            asyncio.create_task(pipe(reader, up, f"client->{label}")),
            asyncio.create_task(pipe(ur, writer, f"{label}->client")),
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
        log.info("RELAY_END route=%s sni=%s", label, sni)
    except asyncio.TimeoutError:
        log.warning("UPSTREAM_TIMEOUT route=%s sni=%s dest=%s:%s", label, sni, dest[0], dest[1])
    except ConnectionError as exc:
        log.warning("UPSTREAM_CONNECT_ERROR route=%s sni=%s dest=%s:%s error=%s", label, sni, dest[0], dest[1], exc)
    except Exception as exc:
        log.warning("RELAY_ERROR route=%s sni=%s error=%s:%s", label, sni, type(exc).__name__, exc)
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
    peer = writer.get_extra_info("peername") or ("-", 0)
    async with SEM:
        try:
            log.info("TCP_ACCEPT peer=%s:%s", peer[0], peer[1])
            initial = await read_initial(reader, peer)
            if not initial:
                log.info("TCP_EMPTY peer=%s:%s", peer[0], peer[1])
                return

            log.info(
                "INITIAL_COMPLETE peer=%s:%s bytes=%d first=0x%s",
                peer[0], peer[1], len(initial), initial[:8].hex(),
            )

            if initial.startswith(HTTP):
                await http(reader, writer, initial)
                return

            tls = initial[:1] == b"\x16" and len(initial) >= 3 and initial[1] == 0x03
            if tls:
                state, sni, reason, hello = _tls_client_hello(initial)
                if state != "COMPLETE":
                    log.warning(
                        "TLS_PARSE_FAIL peer=%s:%s state=%s bytes=%d handshake=%d sni=%s reason=%s",
                        peer[0], peer[1], state, len(initial), len(hello), sni or "-", reason,
                    )
                    return

                log.info(
                    "TLS_CLIENT_HELLO_COMPLETE peer=%s:%s bytes=%d handshake=%d sni=%s",
                    peer[0], peer[1], len(initial), len(hello), sni or "-",
                )
                route = ROUTES.get(sni or "")
                if route:
                    log.info(
                        "ROUTE_SELECTED peer=%s:%s sni=%s route=%s dest=%s:%s",
                        peer[0], peer[1], sni, route[2], route[0], route[1],
                    )
                    await relay(reader, writer, initial, (route[0], route[1]), route[2], sni)
                    return

                log.warning("ROUTE_REJECT peer=%s:%s tls_sni=%s reason=unknown-sni", peer[0], peer[1], sni or "-")
                return

            log.warning(
                "ROUTE_REJECT peer=%s:%s unknown_protocol=0x%s",
                peer[0], peer[1], initial[:1].hex() if initial else "-",
            )
        except Exception as exc:
            log.warning("ERROR peer=%s:%s type=%s error=%s", peer[0], peer[1], type(exc).__name__, exc)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


async def main():
    server = await asyncio.start_server(handle, "0.0.0.0", PORT, limit=65536)
    log.warning("GATEWAY_READY=%s max_connections=%s idle_timeout=%ss loglevel=%s", PORT, MAX_CONNECTIONS, IDLE_TIMEOUT, os.environ.get("GATEWAY_LOGLEVEL", "INFO").upper())
    log.warning("ROUTES=%s", ",".join(f"{k}->{v[1]}" for k, v in ROUTES.items()))
    try:
        await server.serve_forever()
    finally:
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
