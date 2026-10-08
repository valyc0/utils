#!/usr/bin/env python3
"""Tunnel TCP over HTTP (solo libreria standard).

  app --tcp--> HttpTcpClient --HTTP--> HttpTcpServer --tcp--> servizio (es. DB :3128)

Protocollo (una "sessione" per ogni connessione TCP):
  POST /open        -> 200, body = session id (il server si connette al target)
  POST /send/<sid>  -> body = bytes da scrivere sul socket target
  GET  /recv/<sid>  -> long-poll: 200 + bytes | 204 timeout (riprova) | 410 chiusa
  POST /close/<sid> -> chiude la sessione
Tutte le richieste portano l'header X-Token se configurato.

Uso:
  server: python tcp_over_http.py server --listen 0.0.0.0:8080 --target 127.0.0.1:3128 --token SEGRETO
  client: python tcp_over_http.py client --listen 127.0.0.1:3128 --url http://host:8080 --token SEGRETO
"""
import argparse
import http.client
import queue
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

CHUNK = 64 * 1024
POLL_TIMEOUT = 25  # secondi di long-poll
IDLE_TIMEOUT = 300  # sessioni senza attività vengono chiuse


def _nodelay(sock):
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return sock


class _Session:
    def __init__(self, sock):
        self.sock = sock
        self.q = queue.Queue()  # chunk dal target; None = EOF
        self.last = time.time()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        try:
            while data := self.sock.recv(CHUNK):
                self.q.put(data)
        except OSError:
            pass
        self.q.put(None)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class HttpTcpServer:
    """Server HTTP che inoltra i dati verso target (host, port) via TCP."""

    def __init__(self, listen_host, listen_port, target_host, target_port, token=None):
        self.target = (target_host, target_port)
        self.token = token
        self.sessions = {}
        self.lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                super().setup()
                _nodelay(self.request)

            def log_message(self, *a):
                pass

            def _reply(self, code, body=b""):
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _handle(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                if outer.token and self.headers.get("X-Token") != outer.token:
                    return self._reply(403)
                parts = self.path.strip("/").split("/")
                try:
                    code, out = outer._dispatch(self.command, parts, body)
                except Exception:
                    code, out = 502, b""
                self._reply(code, out)

            do_GET = do_POST = _handle

        self.httpd = ThreadingHTTPServer((listen_host, listen_port), Handler)
        self.httpd.daemon_threads = True

    def _dispatch(self, method, parts, body):
        op = parts[0]
        if op == "open" and method == "POST":
            sock = _nodelay(socket.create_connection(self.target, timeout=10))
            sock.settimeout(None)
            sid = secrets.token_urlsafe(16)
            with self.lock:
                self.sessions[sid] = _Session(sock)
            return 200, sid.encode()
        s = self.sessions.get(parts[1]) if len(parts) > 1 else None
        if s is None:
            return 410, b""
        s.last = time.time()
        if op == "send" and method == "POST":
            try:
                s.sock.sendall(body)
            except OSError:
                self._drop(parts[1])
                return 410, b""
            return 200, b""
        if op == "recv" and method == "GET":
            try:
                chunk = s.q.get(timeout=POLL_TIMEOUT)
            except queue.Empty:
                return 204, b""
            if chunk is None:
                s.q.put(None)  # EOF visibile a eventuali poll successivi
                self._drop(parts[1])
                return 410, b""
            buf = [chunk]
            try:  # accorpa i chunk già pronti
                while True:
                    c = s.q.get_nowait()
                    if c is None:
                        s.q.put(None)
                        break
                    buf.append(c)
            except queue.Empty:
                pass
            return 200, b"".join(buf)
        if op == "close" and method == "POST":
            self._drop(parts[1])
            return 200, b""
        return 404, b""

    def _drop(self, sid):
        with self.lock:
            s = self.sessions.pop(sid, None)
        if s:
            s.close()

    def _reaper(self):
        while True:
            time.sleep(30)
            now = time.time()
            for sid, s in list(self.sessions.items()):
                if now - s.last > IDLE_TIMEOUT:
                    self._drop(sid)

    def serve_forever(self):
        threading.Thread(target=self._reaper, daemon=True).start()
        self.httpd.serve_forever()

    def shutdown(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class HttpTcpClient:
    """Ascolta su una porta TCP locale e inoltra tutto al HttpTcpServer via HTTP(S)."""

    def __init__(self, listen_host, listen_port, url, token=None):
        u = urlparse(url)
        self.https = u.scheme == "https"
        self.host = u.hostname
        self.port = u.port or (443 if self.https else 80)
        self.prefix = u.path.rstrip("/")
        self.token = token
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind((listen_host, listen_port))
        self.srv.listen(64)

    def _conn(self):
        cls = http.client.HTTPSConnection if self.https else http.client.HTTPConnection
        conn = cls(self.host, self.port, timeout=POLL_TIMEOUT + 15)
        conn.connect()
        _nodelay(conn.sock)
        return conn

    def _req(self, conn, method, path, body=None):
        headers = {"X-Token": self.token} if self.token else {}
        conn.request(method, self.prefix + path, body=body, headers=headers)
        r = conn.getresponse()
        return r.status, r.read()

    def _handle(self, client):
        _nodelay(client)
        up, down = self._conn(), self._conn()
        try:
            status, sid = self._req(up, "POST", "/open", b"")
            if status != 200:
                return
            sid = sid.decode()
        except Exception:
            client.close()
            return

        done = threading.Event()

        def tcp_to_http():
            try:
                while data := client.recv(CHUNK):
                    status, _ = self._req(up, "POST", f"/send/{sid}", data)
                    if status != 200:
                        break
            except Exception:
                pass
            done.set()
            try:
                client.shutdown(socket.SHUT_RD)
            except OSError:
                pass

        def http_to_tcp():
            try:
                while not done.is_set():
                    status, data = self._req(down, "GET", f"/recv/{sid}")
                    if status == 200:
                        client.sendall(data)
                    elif status != 204:
                        break
            except Exception:
                pass
            done.set()
            try:
                client.shutdown(socket.SHUT_WR)
            except OSError:
                pass

        t1 = threading.Thread(target=tcp_to_http, daemon=True)
        t2 = threading.Thread(target=http_to_tcp, daemon=True)
        t1.start(); t2.start()
        t1.join()
        # lato locale chiuso: avvisa il server e termina
        try:
            self._req(self._conn(), "POST", f"/close/{sid}", b"")
        except Exception:
            pass
        t2.join(timeout=POLL_TIMEOUT + 5)
        client.close()

    def serve_forever(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(c,), daemon=True).start()

    def shutdown(self):
        self.srv.close()


def _hp(s):
    h, _, p = s.rpartition(":")
    return h or "127.0.0.1", int(p)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="TCP over HTTP")
    sub = ap.add_subparsers(dest="mode", required=True)
    s = sub.add_parser("server")
    s.add_argument("--listen", required=True, help="host:port HTTP")
    s.add_argument("--target", required=True, help="host:port TCP di destinazione")
    s.add_argument("--token")
    c = sub.add_parser("client")
    c.add_argument("--listen", required=True, help="host:port TCP locale")
    c.add_argument("--url", required=True, help="URL del server, es. https://host:8080")
    c.add_argument("--token")
    a = ap.parse_args()
    try:
        if a.mode == "server":
            (lh, lp), (th, tp) = _hp(a.listen), _hp(a.target)
            HttpTcpServer(lh, lp, th, tp, a.token).serve_forever()
        else:
            lh, lp = _hp(a.listen)
            HttpTcpClient(lh, lp, a.url, a.token).serve_forever()
    except KeyboardInterrupt:
        pass
