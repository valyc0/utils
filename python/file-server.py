#!/usr/bin/env python3
import os
import sys
import io
import time
import shutil
import zipfile
import pty
import select
import fcntl
import termios
import struct
import uuid
import json
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote
from pathlib import Path
import html
import base64
import hashlib
import struct as _struct
import threading
import signal
import mimetypes
import tarfile
import tempfile

class FileServerHandler(BaseHTTPRequestHandler):
    storage_dir = "storage"
    USERNAME = "admin"
    PASSWORD = "admin"
    enable_terminal = True
    sessions = {}

    # --- Autenticazione HTTP Basic ---
    def do_AUTHHEAD(self):
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm="FileServer"')
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b'Autenticazione richiesta.')

    def authenticate(self):
        auth_header = self.headers.get('Authorization')
        if auth_header is None or not auth_header.startswith('Basic '):
            self.do_AUTHHEAD()
            return False
        encoded = auth_header.split(' ')[1]
        decoded = base64.b64decode(encoded).decode()
        user, passwd = decoded.split(':', 1)
        if user == self.USERNAME and passwd == self.PASSWORD:
            return True
        else:
            self.do_AUTHHEAD()
            return False

    # --- Utilità ---
    def resolve_in_storage(self, name):
        """Risolve `name` (relativo a storage_dir, oppure assoluto). Si può navigare oltre storage_dir."""
        base = Path(self.storage_dir).resolve()
        return (base / (name or "")).resolve()

    @staticmethod
    def sanitize_relative_path(filename):
        """Normalizza un percorso relativo di upload mantenendo le sottocartelle."""
        filename = filename.replace("\\", "/")
        parts = [p for p in filename.split("/") if p not in ("", ".", "..")]
        return "/".join(parts) if parts else None

    def read_json(self):
        """Legge il body JSON della richiesta; risponde 400 e ritorna None se non valido."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self.send_error(400, "JSON non valido")
            return None
        if not isinstance(payload, dict):
            self.send_error(400, "JSON non valido")
            return None
        return payload

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def unique_path(path):
        """Se `path` esiste già, ritorna 'nome (1).ext', 'nome (2).ext'..."""
        if not path.exists():
            return path
        n = 1
        while True:
            cand = path.with_name(f"{path.stem} ({n}){path.suffix}")
            if not cand.exists():
                return cand
            n += 1

    @staticmethod
    def build_zip(paths):
        """ZIP (in file temporaneo, non in RAM) di file/cartelle, con percorsi relativi al loro genitore."""
        tmp = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in paths:
                base = p.parent
                if p.is_dir():
                    for root, _, fnames in os.walk(p):
                        for fname in fnames:
                            full = Path(root) / fname
                            try:
                                zf.write(full, full.relative_to(base).as_posix())
                            except OSError:
                                pass
                else:
                    try:
                        zf.write(p, p.name)
                    except OSError:
                        pass
        size = tmp.tell()
        tmp.seek(0)
        return tmp, size

    def send_zip(self, paths, zip_name):
        tmp, size = self.build_zip(paths)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{zip_name}.zip"')
            self.send_header("Content-Length", str(size))
            self.end_headers()
            shutil.copyfileobj(tmp, self.wfile)
        finally:
            tmp.close()

    @staticmethod
    def mtime_us(path):
        return path.stat().st_mtime_ns // 1000

    @staticmethod
    def is_text_file(path, limit=512 * 1024):
        try:
            if path.stat().st_size > limit:
                return False
            with path.open("rb") as fh:
                return b"\x00" not in fh.read(4096)
        except OSError:
            return False

    @staticmethod
    def format_size(num):
        """Formatta una dimensione in byte in forma leggibile (B, KB, MB...)."""
        size = float(num)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024 or unit == "TB":
                return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024

    @staticmethod
    def format_date(ts):
        """Formatta un timestamp in data/ora leggibile (gg/mm/aaaa hh:mm)."""
        return time.strftime("%d/%m/%Y %H:%M", time.localtime(ts))

    # --- WebSocket minimal implementation ---
    WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    def ws_handshake(self):
        key = self.headers.get("Sec-WebSocket-Key", "").strip()
        if not key:
            return False
        accept = base64.b64encode(
            hashlib.sha1((key + self.WS_GUID).encode()).digest()
        ).decode()
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self._ws_lock = threading.Lock()
        return True

    def ws_read_frame(self):
        try:
            b1 = self.rfile.read(1)
            if not b1:
                return None, None
            b2 = self.rfile.read(1)
            if not b2:
                return None, None
            opcode = b1[0] & 0x0F
            masked = (b2[0] & 0x80) != 0
            length = b2[0] & 0x7F
            if length == 126:
                raw = self.rfile.read(2)
                if len(raw) < 2:
                    return None, None
                length = struct.unpack("!H", raw)[0]
            elif length == 127:
                raw = self.rfile.read(8)
                if len(raw) < 8:
                    return None, None
                length = struct.unpack("!Q", raw)[0]
            mask_key = None
            if masked:
                mask_key = self.rfile.read(4)
                if len(mask_key) < 4:
                    return None, None
            payload = b""
            while len(payload) < length:
                chunk = self.rfile.read(length - len(payload))
                if not chunk:
                    return None, None
                payload += chunk
            if mask_key:
                payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
            if opcode == 0x08:
                return "close", b""
            return opcode, payload
        except (OSError, ValueError):
            return None, None

    def ws_send_frame(self, opcode, payload):
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        frame = bytearray()
        frame.append(0x80 | opcode)
        length = len(payload)
        if length < 126:
            frame.append(length)
        elif length < 65536:
            frame.append(126)
            frame.extend(struct.pack("!H", length))
        else:
            frame.append(127)
            frame.extend(struct.pack("!Q", length))
        frame.extend(payload)
        try:
            with self._ws_lock:
                self.wfile.write(bytes(frame))
                self.wfile.flush()
        except (OSError, ValueError):
            pass

    def ws_send_json(self, obj):
        self.ws_send_frame(0x01, json.dumps(obj))

    def ws_handle(self):
        if not self.ws_handshake():
            return
        sessions = FileServerHandler.sessions
        attached = set()
        try:
            while True:
                opcode, payload = self.ws_read_frame()
                if opcode is None:
                    break
                if opcode == "close":
                    break
                try:
                    msg = json.loads(payload.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue
                msg_type = msg.get("type")
                if msg_type == "new":
                    cwd = self.resolve_in_storage(msg.get("cwd") or "")
                    if not cwd.is_dir():
                        cwd = Path(self.storage_dir).resolve()
                    pid, master_fd = pty.fork()
                    if pid == 0:
                        os.chdir(cwd)
                        env = os.environ.copy()
                        env["TERM"] = "xterm-256color"
                        os.execvpe("/bin/bash", ["/bin/bash", "--norc", "--noprofile"], env)
                    sid = uuid.uuid4().hex
                    sess = {"pid": pid, "fd": master_fd, "dir": str(cwd), "buf": bytearray(), "subs": {self}, "lock": threading.Lock()}
                    sessions[sid] = sess
                    attached.add(sid)
                    cols = msg.get("cols", 80)
                    rows = msg.get("rows", 24)
                    try:
                        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
                    except (ValueError, OSError):
                        pass
                    self.ws_send_json({"type": "new", "sid": sid})
                    threading.Thread(target=FileServerHandler.ws_pty_reader, args=(sid, sess), daemon=True).start()
                elif msg_type == "attach":
                    # Collega questa connessione a una shell già esistente, riproducendo lo scrollback
                    sid = msg.get("sid")
                    sess = sessions.get(sid)
                    if not sess:
                        self.ws_send_json({"type": "exit", "sid": sid})
                        continue
                    try:
                        fcntl.ioctl(sess["fd"], termios.TIOCSWINSZ,
                                    struct.pack("HHHH", int(msg.get("rows", 24)), int(msg.get("cols", 80)), 0, 0))
                    except (ValueError, OSError):
                        pass
                    with sess["lock"]:
                        sess["subs"].add(self)
                        attached.add(sid)
                        self.ws_send_json({"type": "attached", "sid": sid})
                        if sess["buf"]:
                            self.ws_send_json({"type": "output", "sid": sid, "data": base64.b64encode(bytes(sess["buf"])).decode()})
                elif msg_type == "input":
                    sid = msg.get("sid")
                    data = msg.get("data", "")
                    sess = sessions.get(sid)
                    if sess:
                        try:
                            os.write(sess["fd"], data.encode("utf-8") if isinstance(data, str) else data)
                        except OSError:
                            pass
                elif msg_type == "resize":
                    sid = msg.get("sid")
                    sess = sessions.get(sid)
                    if sess:
                        try:
                            cols = int(msg.get("cols", 80))
                            rows = int(msg.get("rows", 24))
                            fcntl.ioctl(sess["fd"], termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
                        except (ValueError, OSError):
                            pass
                elif msg_type == "close":
                    sid = msg.get("sid")
                    sess = sessions.pop(sid, None)
                    if sess:
                        try:
                            os.kill(sess["pid"], signal.SIGHUP)
                        except OSError:
                            pass
                        try:
                            os.close(sess["fd"])
                        except OSError:
                            pass
        except (OSError, ValueError):
            pass
        finally:
            # Le shell sopravvivono alla chiusura della connessione (es. altro tab): ci si stacca soltanto
            for sid in attached:
                sess = sessions.get(sid)
                if sess:
                    with sess["lock"]:
                        sess["subs"].discard(self)

    # --- Thread lettore del PTY (uno per sessione, indipendente dalle connessioni) ---
    @staticmethod
    def ws_pty_reader(sid, sess):
        sessions = FileServerHandler.sessions
        fd = sess["fd"]
        while sessions.get(sid) is sess:
            try:
                r, _, _ = select.select([fd], [], [], 0.2)
                if not r:
                    continue
                data = os.read(fd, 8192)
            except (OSError, ValueError):
                break
            if not data:
                break
            with sess["lock"]:
                sess["buf"] += data
                if len(sess["buf"]) > 262144:
                    del sess["buf"][:-262144]
                msg = {"type": "output", "sid": sid, "data": base64.b64encode(data).decode()}
                for sub in list(sess["subs"]):
                    try:
                        sub.ws_send_json(msg)
                    except Exception:
                        sess["subs"].discard(sub)
        if sessions.get(sid) is sess:
            sessions.pop(sid, None)
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.waitpid(sess["pid"], os.WNOHANG)
            except OSError:
                pass
            for sub in list(sess["subs"]):
                try:
                    sub.ws_send_json({"type": "exit", "sid": sid})
                except Exception:
                    pass

    @staticmethod
    def safe_extract(arc, dest):
        """Estrae zip/tar in `dest` rifiutando percorsi che escono da dest (zip-slip) e link/special file."""
        dest = dest.resolve()

        def check(name):
            out = (dest / name).resolve()
            if out != dest and dest not in out.parents:
                raise ValueError(f"percorso non sicuro nell'archivio: {name}")

        count = 0
        if zipfile.is_zipfile(arc):
            with zipfile.ZipFile(arc) as zf:
                for info in zf.infolist():
                    check(info.filename)
                for info in zf.infolist():
                    zf.extract(info, dest)
                    count += 0 if info.is_dir() else 1
        elif tarfile.is_tarfile(arc):
            with tarfile.open(arc) as tf:
                members = tf.getmembers()
                for m in members:
                    check(m.name)
                    if not (m.isfile() or m.isdir()):
                        raise ValueError(f"tipo di voce non supportato: {m.name}")
                for m in members:
                    tf.extract(m, dest)
                    count += 1 if m.isfile() else 0
        else:
            raise ValueError("formato non supportato (zip, tar, tar.gz, tar.bz2, tar.xz)")
        return count

    def handle_search(self, params):
        base = self.resolve_in_storage(params.get("path", [""])[0])
        q = params.get("q", [""])[0].strip().lower()
        by_content = params.get("content", ["0"])[0] == "1"
        if not q or not base.is_dir():
            return self.send_error(400, "Parametri non validi")
        results, deadline, truncated = [], time.time() + 5, False
        for root, dnames, fnames in os.walk(base):
            if time.time() > deadline or len(results) >= 200:
                truncated = True
                break
            for name in sorted(dnames) + sorted(fnames):
                full = Path(root) / name
                is_dir = name in dnames
                if not by_content and q in name.lower():
                    results.append({"path": str(full), "type": "dir" if is_dir else "file"})
                elif by_content and not is_dir and self.is_text_file(full):
                    try:
                        text = full.read_bytes().decode("utf-8", errors="replace")
                    except OSError:
                        continue
                    for i, line in enumerate(text.splitlines(), 1):
                        if q in line.lower():
                            results.append({"path": str(full), "type": "file", "line": i, "snippet": line.strip()[:160]})
                            break
                if len(results) >= 200:
                    truncated = True
                    break
        self.send_json({"results": results, "truncated": truncated})

    # --- Gestione GET ---
    def do_GET(self):
        if not self.authenticate():
            return

        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        self.params = params

        if parsed.path == "/":
            browse_path = params.get("path", [""])[0]
            return self.send_index_page(browse_path, params.get("view", [""])[0], params.get("file", [""])[0])

        elif parsed.path == "/list":
            entries = sorted(p.name for p in Path(self.storage_dir).iterdir())
            data = "\n".join(entries).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        elif parsed.path == "/download":
            filename = params.get("file", [None])[0]
            if not filename:
                return self.send_error(400, "Parametro 'file' mancante. Usa ?file=nomefile")
            file_path = self.resolve_in_storage(filename)
            if not file_path.is_file():
                return self.send_error(404, "File non trovato")
            inline = params.get("inline", ["0"])[0] == "1"
            ctype = (mimetypes.guess_type(file_path.name)[0] or "application/octet-stream") if inline else "application/octet-stream"
            if inline and ctype in ("text/html", "image/svg+xml", "application/xhtml+xml"):
                ctype = "text/plain"  # niente HTML/SVG eseguibile nell'origine del server
            try:
                f = open(file_path, "rb")
            except OSError as e:
                return self.send_error(403, f"Impossibile leggere il file: {e}")
            with f:
                total = file_path.stat().st_size
                start_b, end_b, code = 0, total - 1, 200
                rng = self.headers.get("Range", "")
                if rng.startswith("bytes=") and "," not in rng:
                    a, _, b = rng[6:].partition("-")
                    try:
                        if a == "":
                            start_b = max(0, total - int(b))
                        else:
                            start_b = int(a)
                            if b:
                                end_b = min(int(b), total - 1)
                        if start_b > end_b or start_b >= total:
                            self.send_response(416)
                            self.send_header("Content-Range", f"bytes */{total}")
                            self.send_header("Content-Length", "0")
                            self.end_headers()
                            return
                        code = 206
                    except ValueError:
                        start_b, end_b = 0, total - 1
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                if code == 206:
                    self.send_header("Content-Range", f"bytes {start_b}-{end_b}/{total}")
                disp = "inline" if inline else "attachment"
                self.send_header("Content-Disposition", f'{disp}; filename="{file_path.name}"')
                self.send_header("Content-Length", str(end_b - start_b + 1))
                self.end_headers()
                f.seek(start_b)
                remaining = end_b - start_b + 1
                try:
                    while remaining > 0:
                        chunk = f.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        elif parsed.path == "/download-dir":
            dirname = params.get("dir", [None])[0]
            if not dirname:
                return self.send_error(400, "Parametro 'dir' mancante. Usa ?dir=nomecartella")
            dir_path = self.resolve_in_storage(dirname)
            if not dir_path.is_dir():
                return self.send_error(404, "Directory non trovata")
            self.send_zip([dir_path], dir_path.name or "root")

        elif parsed.path == "/download-multi":
            paths = [self.resolve_in_storage(x) for x in params.get("p", [])]
            paths = [x for x in paths if x.exists()]
            if not paths:
                return self.send_error(404, "Nessun elemento valido")
            self.send_zip(paths, "selezione")

        elif parsed.path == "/search":
            return self.handle_search(params)

        elif parsed.path == "/delete":
            filename = params.get("file", [None])[0]
            if not filename:
                return self.send_error(400, "Parametro 'file' mancante per cancellazione")
            target = self.resolve_in_storage(filename)
            if not target.exists():
                return self.send_error(404, "File o cartella non trovato")
            if target == target.parent:
                return self.send_error(403, "Impossibile cancellare la root del filesystem")
            try:
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
                self.send_response(303)
                self.send_header("Location", "/")
                self.end_headers()
            except Exception as e:
                self.send_error(500, f"Errore durante la cancellazione: {e}")

        elif parsed.path == "/edit":
            filename = params.get("file", [None])[0]
            if not filename:
                return self.send_error(400, "Parametro 'file' mancante. Usa ?file=nomefile")
            target = self.resolve_in_storage(filename)
            if not target.is_file():
                return self.send_error(404, "File non trovato")
            if target.stat().st_size > 512 * 1024:
                return self.send_error(413, "File troppo grande per l'editor (max 512KB)")
            try:
                data = target.read_bytes()
            except OSError as e:
                return self.send_error(403, f"Impossibile leggere il file: {e}")
            if b"\x00" in data:
                return self.send_error(400, "File binario, non modificabile con l'editor")
            self.send_json({"name": str(target), "content": data.decode("utf-8", errors="replace"), "mtime": self.mtime_us(target)})

        elif parsed.path.startswith("/static/"):
            rel = parsed.path[len("/static/"):]
            base = Path(__file__).resolve().parent / "static"
            target = (base / rel).resolve()
            if base != target and base not in target.parents:
                return self.send_error(403, "Forbidden")
            if not target.is_file():
                return self.send_error(404, "Not found")
            ctype = {
                ".js": "application/javascript",
                ".css": "text/css",
                ".map": "application/json",
            }.get(target.suffix, "application/octet-stream")
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(data)

        elif parsed.path == "/term/ws":
            if not self.enable_terminal:
                return self.send_error(404, "Terminale disabilitato")
            return self.ws_handle()

        else:
            self.send_error(404, "Not found")

    # --- Gestione POST per upload (multipli file/cartelle) ---
    def do_POST(self):
        if not self.authenticate():
            return

        parsed = urlparse(self.path)
        if parsed.path == "/save":
            if "application/json" not in self.headers.get("Content-Type", ""):
                return self.send_error(400, "Content-Type deve essere application/json")
            payload = self.read_json()
            if payload is None:
                return
            filename = payload.get("path")
            content = payload.get("content")
            if not isinstance(filename, str) or not isinstance(content, str):
                return self.send_error(400, "Campi 'path' e 'content' obbligatori (stringhe)")
            target = self.resolve_in_storage(filename)
            expected = payload.get("mtime")
            if expected is not None and not payload.get("force") and target.exists():
                if self.mtime_us(target) != expected:
                    return self.send_json({"error": "Il file è stato modificato su disco", "mtime": self.mtime_us(target)}, 409)
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                # scrittura atomica: file temporaneo nella stessa cartella + replace
                fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".fs-save-")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8", newline="") as out:
                        out.write(content)
                    if target.exists():
                        shutil.copymode(target, tmp_name)
                    os.replace(tmp_name, target)
                except BaseException:
                    if os.path.exists(tmp_name):
                        os.unlink(tmp_name)
                    raise
            except OSError as e:
                return self.send_error(500, f"Errore durante il salvataggio: {e}")
            return self.send_json({"mtime": self.mtime_us(target)})

        if parsed.path == "/new":
            payload = self.read_json()
            if payload is None:
                return
            name = payload.get("path")
            kind = payload.get("type", "file")
            if not isinstance(name, str) or not name.strip() or kind not in ("file", "dir"):
                return self.send_error(400, "Parametri non validi")
            target = self.resolve_in_storage(name.strip())
            if target.exists():
                return self.send_error(409, "Esiste già un file o una cartella con questo nome")
            try:
                if kind == "dir":
                    target.mkdir(parents=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.touch()
            except OSError as e:
                return self.send_error(500, f"Errore durante la creazione: {e}")
            return self.send_json({"path": str(target), "type": kind, "mtime": self.mtime_us(target)})

        if parsed.path in ("/rename", "/copy"):
            payload = self.read_json()
            if payload is None:
                return
            src, dst = payload.get("src"), payload.get("dst")
            if not isinstance(src, str) or not isinstance(dst, str) or not dst.strip():
                return self.send_error(400, "Parametri non validi")
            src_p = self.resolve_in_storage(src)
            dst_p = self.resolve_in_storage(dst.strip())
            if not src_p.exists():
                return self.send_error(404, "Origine non trovata")
            if dst_p.is_dir() and not src_p == dst_p:
                dst_p = dst_p / src_p.name  # destinazione = cartella → sposta/copia dentro
            if dst_p.exists():
                return self.send_error(409, "La destinazione esiste già")
            if src_p == src_p.parent or dst_p == src_p or src_p in dst_p.parents:
                return self.send_error(400, "Operazione non valida (destinazione dentro l'origine)")
            try:
                dst_p.parent.mkdir(parents=True, exist_ok=True)
                if parsed.path == "/rename":
                    shutil.move(str(src_p), str(dst_p))
                elif src_p.is_dir():
                    shutil.copytree(src_p, dst_p, symlinks=True)
                else:
                    shutil.copy2(src_p, dst_p)
            except (OSError, shutil.Error) as e:
                return self.send_error(500, f"Errore: {e}")
            return self.send_json({"path": str(dst_p)})

        if parsed.path == "/delete-multi":
            payload = self.read_json()
            if payload is None:
                return
            paths = payload.get("paths")
            if not isinstance(paths, list):
                return self.send_error(400, "Parametri non validi")
            deleted, errors = [], []
            for name in paths:
                t = self.resolve_in_storage(name) if isinstance(name, str) else None
                if t is None or not t.exists() or t == t.parent:
                    errors.append(str(name))
                    continue
                try:
                    shutil.rmtree(t) if t.is_dir() and not t.is_symlink() else t.unlink()
                    deleted.append(str(t))
                except OSError:
                    errors.append(str(t))
            return self.send_json({"deleted": deleted, "errors": errors})

        if parsed.path == "/extract":
            payload = self.read_json()
            if payload is None:
                return
            name = payload.get("path")
            if not isinstance(name, str):
                return self.send_error(400, "Parametri non validi")
            arc = self.resolve_in_storage(name)
            if not arc.is_file():
                return self.send_error(404, "Archivio non trovato")
            stem = arc.name
            for ext in (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tar", ".zip"):
                if stem.lower().endswith(ext):
                    stem = stem[:-len(ext)]
                    break
            dest = self.unique_path(arc.parent / (stem or "estratto"))
            try:
                dest.mkdir(parents=True)
                count = self.safe_extract(arc, dest)
            except (OSError, zipfile.BadZipFile, tarfile.TarError, ValueError) as e:
                shutil.rmtree(dest, ignore_errors=True)
                return self.send_error(400, f"Estrazione non riuscita: {e}")
            return self.send_json({"path": str(dest), "files": count})

        if parsed.path != "/upload":
            self.send_error(404, "Not found")
            return

        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            return self.send_error(400, "Upload non valido")

        boundary = ctype.split("boundary=")[1].strip().encode()
        remainbytes = int(self.headers['Content-length'])

        # La prima riga deve contenere il boundary iniziale
        line = self.rfile.readline()
        remainbytes -= len(line)
        if boundary not in line:
            return self.send_error(400, "Malformed form data")

        saved = 0
        renamed = []
        overwrite = parse_qs(parsed.query).get("overwrite", ["0"])[0] == "1"
        upload_dir = self.resolve_in_storage("")
        while remainbytes > 0:
            line = self.rfile.readline()
            remainbytes -= len(line)
            if boundary in line:
                if line.strip().endswith(b"--"):
                    break  # boundary finale
                continue  # boundary di inizio part

            # Intestazioni del part
            filename = None
            field_name = None
            while line.strip() != b"":
                if b'filename="' in line:
                    filename = line.decode(errors="replace").split('filename="')[1].split('"')[0]
                if b'name="' in line:
                    field_name = line.decode(errors="replace").split('name="')[1].split('"')[0]
                line = self.rfile.readline()
                remainbytes -= len(line)

            if filename is None and field_name == "path":
                # Campo 'path' (senza file): legge la cartella di destinazione
                preline = self.rfile.readline()
                remainbytes -= len(preline)
                value = b""
                while True:
                    line = self.rfile.readline()
                    remainbytes -= len(line)
                    if boundary in line:
                        value += preline.rstrip(b'\r\n')
                        break
                    value += preline
                    preline = line
                upload_dir = self.resolve_in_storage(value.decode(errors="replace"))
                continue

            rel = self.sanitize_relative_path(filename) if filename else None
            if rel:
                outpath = upload_dir / rel
                outpath.parent.mkdir(parents=True, exist_ok=True)
                if not overwrite:
                    final = self.unique_path(outpath)
                    if final != outpath:
                        renamed.append({"from": outpath.name, "to": final.name})
                    outpath = final
                with open(outpath, 'wb') as out:
                    preline = self.rfile.readline()
                    remainbytes -= len(preline)
                    while remainbytes > 0:
                        line = self.rfile.readline()
                        remainbytes -= len(line)
                        if boundary in line:
                            preline = preline.rstrip(b'\r\n')
                            out.write(preline)
                            break
                        else:
                            out.write(preline)
                            preline = line
                saved += 1
            else:
                # Campo senza file (o nome non valido): salta il corpo fino al boundary
                while True:
                    line = self.rfile.readline()
                    remainbytes -= len(line)
                    if boundary in line:
                        break

        self.send_json({"saved": saved, "renamed": renamed})

    # --- Pagina HTML principale ---
    def send_index_page(self, browse_path="", view="", open_file=""):
        current = self.resolve_in_storage(browse_path)
        if not current.is_dir():
            return self.send_error(404, "Cartella non trovata")
        try:
            entries = sorted(
                current.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower())
            )
        except OSError as e:
            return self.send_error(403, f"Cartella non leggibile: {e}")

        esc = lambda s: html.escape(str(s), quote=True)
        cur_str = str(current)

        # Breadcrumb assoluto per la navigazione (anche sopra la cartella di partenza)
        acc = Path(current.anchor)
        crumbs = [f"<a href='#' class='nav-link' data-path='{esc(acc)}'>💽 /</a>"]
        for part in current.parts[1:]:
            acc = acc / part
            crumbs.append(f"<a href='#' class='nav-link' data-path='{esc(acc)}'>{esc(part)}</a>")
        crumb_html = " / ".join(crumbs)
        if current.parent != current:
            crumb_html = f"<a href='#' class='nav-link' data-path='{esc(current.parent)}' title='Cartella superiore'>⬆️ Su</a> &nbsp;|&nbsp; " + crumb_html
        crumb_html +=f" &nbsp;<a href='/download-dir?dir={quote(cur_str)}'>⬇️ ZIP</a>"

        PREVIEW_EXT = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "pdf", "mp4", "webm", "ogv", "mp3", "wav", "m4a", "ogg"}
        ARCHIVE_EXT = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")
        rows = ""
        for p in entries:
            name = p.name
            rel = str(p)
            try:
                st = p.stat()
            except OSError:
                continue
            is_dir = p.is_dir()
            size = "—" if is_dir else self.format_size(st.st_size)
            date = self.format_date(st.st_mtime)
            tr = (f"<tr data-name='{esc(name)}' data-dir='{1 if is_dir else 0}' "
                  f"data-size='{0 if is_dir else st.st_size}' data-mtime='{int(st.st_mtime)}'>"
                  f"<td><input type='checkbox' class='sel' data-path='{esc(rel)}'></td>")
            common = (f"<a href='#' class='ren-link' data-path='{esc(rel)}' title='Rinomina / sposta'>✏️</a>"
                      f"<a href='#' class='cp-link' data-path='{esc(rel)}' title='Copia'>⧉</a>"
                      f"<a href='#' class='del-link' data-path='{esc(rel)}' data-label='{esc(name)}' title='Elimina'>🗑</a>")
            if is_dir:
                rows += (
                    tr + f"<td>📁 <a href='#' class='nav-link' data-path='{esc(rel)}'>{esc(name)}/</a></td>"
                    f"<td>{size}</td><td>{date}</td>"
                    f"<td class='acts'><a href='/download-dir?dir={quote(rel)}' title='Scarica ZIP'>ZIP</a>{common}</td></tr>"
                )
            else:
                lower = name.lower()
                ext = lower.rsplit(".", 1)[-1] if "." in lower else ""
                if self.is_text_file(p):
                    name_link = f"<a href='#' class='edit-link' data-file='{esc(rel)}' title='Apri nell&#39;editor'>{esc(name)}</a>"
                elif ext in PREVIEW_EXT:
                    name_link = f"<a href='#' class='prev-link' data-path='{esc(rel)}' title='Anteprima'>{esc(name)}</a>"
                else:
                    name_link = f"<a href='/download?file={quote(rel)}'>{esc(name)}</a>"
                extra = ""
                if ext in PREVIEW_EXT:
                    extra += f"<a href='#' class='prev-link' data-path='{esc(rel)}' title='Anteprima'>👁</a>"
                if lower.endswith(ARCHIVE_EXT):
                    extra += f"<a href='#' class='ext-link' data-path='{esc(rel)}' title='Estrai qui'>📦</a>"
                rows += (
                    tr + f"<td>📄 {name_link}</td>"
                    f"<td>{size}</td><td>{date}</td>"
                    f"<td class='acts'><a href='/download?file={quote(rel)}' title='Scarica'>⬇️</a>{extra}{common}</td></tr>"
                )

        index_template = """<!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>File Server</title>
            <link rel="stylesheet" href="/static/xterm/xterm.css">
            <link rel="stylesheet" href="/static/codemirror/lib/codemirror.css">
            <style>
                body { font-family: sans-serif; margin: 0; height: 100vh; display: flex; flex-direction: column; background: #fafafa; }
                header { display: flex; align-items: center; justify-content: space-between; padding: 12px 20px; background: #263238; color: #fff; flex-shrink: 0; }
                header h1 { margin: 0; font-size: 1.2em; }
                #term-toggle { padding: 8px 14px; border: none; border-radius: 6px; background: #4caf50; color: #fff; font-size: 0.95em; cursor: pointer; }
                #term-toggle:hover { background: #388e3c; }
                #layout { flex: 1; display: flex; min-height: 0; }
                #left-panel { width: 420px; min-width: 260px; flex-shrink: 0; display: flex; background: #fff; }
                #left-scroll { flex: 1; overflow: auto; padding: 16px 20px; }
                #vsplitter { width: 6px; cursor: col-resize; background: #cfd8dc; flex-shrink: 0; }
                #vsplitter:hover { background: #90a4ae; }
                #right-panel { flex: 1; min-width: 0; display: flex; flex-direction: column; background: #fff; }
                #term-wrap { display: none; flex-direction: column; min-height: 120px; flex-shrink: 0; }
                #term-wrap.active { display: flex; }
                #hsplitter { display: none; height: 6px; cursor: row-resize; background: #cfd8dc; flex-shrink: 0; }
                #hsplitter.active { display: block; }
                #hsplitter:hover { background: #90a4ae; }
                #editor-panel { flex: 1; min-height: 0; display: flex; flex-direction: column; }
                #editor-tabs { display: flex; gap: 4px; padding: 6px 10px; background: #37474f; overflow-x: auto; flex-shrink: 0; }
                .ed-tab { display: flex; align-items: center; gap: 6px; padding: 5px 10px; background: #546e7a; color: #fff; border-radius: 6px 6px 0 0; cursor: pointer; font-size: 0.85em; white-space: nowrap; max-width: 220px; user-select: none; }
                .ed-tab.active { background: #263238; }
                .ed-tab span { overflow: hidden; text-overflow: ellipsis; }
                .ed-tab-close { color: #cfd8dc; font-weight: bold; }
                .ed-tab-close:hover { color: #ef5350; }
                #editor-bar { display: flex; align-items: center; gap: 10px; padding: 10px 14px; background: #eceff1; border-bottom: 1px solid #cfd8dc; }
                #editor-title { font-weight: bold; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 0.95em; }
                #editor-status { font-size: 0.85em; color: #f9a825; }
                #editor-save { padding: 6px 12px; border: none; border-radius: 6px; cursor: pointer; font-size: 0.9em; background: #4caf50; color: #fff; }
                #editor-save:hover { background: #388e3c; }
                #editor-host { flex: 1; position: relative; overflow: hidden; }
                #editor-host .CodeMirror { position: absolute; inset: 0; height: 100%; font-size: 14px; }
                #editor-placeholder { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; color: #90a4ae; font-size: 1.1em; text-align: center; }
                input[type=file] { margin: 10px 0; }
                table { border-collapse: collapse; margin-top: 10px; width: 100%; }
                th, td { text-align: left; padding: 4px 10px 4px 0; border-bottom: 1px solid #eee; font-size: 0.92em; }
                th { font-size: 0.85em; color: #666; }
                #drop-zone { border: 2px dashed #aaa; border-radius: 8px; padding: 24px 16px; text-align: center; color: #666; margin: 16px 0; cursor: pointer; font-size: 0.95em; }
                #drop-zone.dragover { border-color: #4caf50; background: #e8f5e9; color: #2e7d32; }
                #status { display: none; margin: 10px 0; padding: 10px; background: #fff3cd; border-radius: 4px; }
                .progress { display: none; width: 100%; background: #eee; border-radius: 4px; height: 16px; margin: 10px 0; }
                .progress > div { height: 100%; width: 0%; background: #4caf50; border-radius: 4px; }
                #crumbs { margin: 8px 0 12px; padding: 8px 12px; background: #f5f5f5; border-radius: 6px; font-size: 0.9em; word-break: break-all; }
                #crumbs a { color: #1976d2; text-decoration: none; }
                #crumbs a:hover { text-decoration: underline; }
                #term-bar { display: flex; align-items: center; padding: 8px 10px 0; gap: 4px; background: #263238; }
                #term-tabs { display: flex; gap: 4px; flex-wrap: wrap; }
                .term-tab { padding: 6px 12px; border: 1px solid #455a64; border-bottom: none; border-radius: 6px 6px 0 0; background: #eceff1; cursor: pointer; font-size: 0.9em; user-select: none; }
                .term-tab.active { background: #263238; color: #fff; }
                .term-tab .close { margin-left: 8px; color: #999; cursor: pointer; font-weight: bold; }
                .term-tab .close:hover { color: #d32f2f; }
                #term-add { padding: 6px 12px; border: none; border-radius: 6px; background: #4caf50; color: #fff; cursor: pointer; font-size: 0.9em; margin-left: 4px; }
                #term-add:hover { background: #388e3c; }
                #term-container { flex: 1; min-height: 0; padding: 0 8px 8px; }
                .term-view { display: none; position: relative; height: 100%; min-height: 120px; padding: 8px; background: #000; border-radius: 0 6px 6px 6px; border: 1px solid #455a64; box-sizing: border-box; }
                .term-view.active { display: block; }
                body.full-term #left-panel, body.full-term #vsplitter, body.full-term #editor-panel, body.full-term #hsplitter, body.full-term header,
                body.full-editor #left-panel, body.full-editor #vsplitter, body.full-editor #term-wrap, body.full-editor #hsplitter, body.full-editor header { display: none !important; }
                body.full-term #term-wrap { height: 100% !important; flex: 1; }
                body.full-term #term-container { padding: 0; }
                #header-btns { display: flex; gap: 8px; }
                #term-newtab { padding: 8px 12px; border: none; border-radius: 6px; background: #1976d2; color: #fff; font-size: 0.95em; cursor: pointer; }
                th.sortable, th[data-sort] { cursor: pointer; user-select: none; white-space: nowrap; }
                th[data-sort]:hover { color: #1976d2; }
                td.acts a { margin-right: 6px; text-decoration: none; white-space: nowrap; }
                #tools { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 8px 0; font-size: 0.88em; }
                #tools input[type=text] { padding: 4px 8px; border: 1px solid #b0bec5; border-radius: 6px; }
                #filter-input { flex: 1 1 120px; }
                #search-form { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin: 8px 0; font-size: 0.88em; }
                #search-q { flex: 1 1 140px; padding: 4px 8px; border: 1px solid #b0bec5; border-radius: 6px; }
                #search-form button { padding: 5px 10px; border: none; border-radius: 6px; background: #607d8b; color: #fff; cursor: pointer; }
                #search-results { display: none; max-height: 220px; overflow: auto; border: 1px solid #cfd8dc; border-radius: 6px; margin: 6px 0; font-size: 0.88em; }
                .sr-head { padding: 6px 8px; background: #eceff1; position: sticky; top: 0; }
                .sr-item { padding: 4px 8px; cursor: pointer; border-bottom: 1px solid #eee; word-break: break-all; }
                .sr-item:hover { background: #e3f2fd; }
                .sr-snip { color: #666; font-family: monospace; font-size: 0.9em; }
                #sel-bar { display: none; align-items: center; gap: 8px; flex-wrap: wrap; padding: 6px 8px; margin: 6px 0; background: #fff8e1; border: 1px solid #ffe082; border-radius: 6px; font-size: 0.88em; }
                #sel-bar button { padding: 4px 10px; border: none; border-radius: 6px; background: #607d8b; color: #fff; cursor: pointer; }
                #find-bar { display: none; align-items: center; gap: 6px; flex-wrap: wrap; padding: 6px 10px; background: #eceff1; border-bottom: 1px solid #cfd8dc; font-size: 0.88em; }
                #find-bar input[type=text] { padding: 4px 8px; border: 1px solid #b0bec5; border-radius: 6px; width: 160px; }
                #find-bar button { padding: 4px 9px; border: none; border-radius: 6px; background: #607d8b; color: #fff; cursor: pointer; }
                #find-info { color: #c62828; }
                #editor-bar label { font-size: 0.85em; color: #455a64; white-space: nowrap; }
                #overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.6); z-index: 1000; display: flex; align-items: center; justify-content: center; }
                .ov-box { background: #fff; border-radius: 8px; width: min(1000px, 94vw); height: min(80vh, 900px); display: flex; flex-direction: column; overflow: hidden; }
                .ov-head { display: flex; justify-content: space-between; align-items: center; padding: 8px 12px; background: #263238; color: #fff; }
                .ov-head button { background: none; border: none; color: #fff; font-size: 1.1em; cursor: pointer; }
                .ov-body { flex: 1; min-height: 0; border: 0; width: 100%; overflow: auto; background: #fff; }
                img.ov-body, video.ov-body { object-fit: contain; background: #111; }
                pre.diff { margin: 0; padding: 8px 12px; font-size: 13px; box-sizing: border-box; }
                .dadd { background: #e6ffed; display: block; } .ddel { background: #ffeef0; display: block; } .dctx { color: #666; display: block; }
                #new-form { display: flex; flex-wrap: wrap; gap: 6px; margin: 12px 0; }
                #new-name { flex: 1 1 100%; padding: 6px 8px; border: 1px solid #b0bec5; border-radius: 6px; font-size: 0.9em; }
                #new-form button { padding: 7px 12px; border: none; border-radius: 6px; background: #7b1fa2; color: #fff; font-size: 0.9em; cursor: pointer; }
                #new-form button:hover { background: #6a1b9a; }
                .ed-tab.dirty .ed-label::before { content: "● "; color: #ffb74d; }
                #editor-bar .ed-btn { padding: 6px 12px; border: none; border-radius: 6px; cursor: pointer; font-size: 0.9em; background: #607d8b; color: #fff; }
                #editor-bar .ed-btn:hover { background: #455a64; }
            </style>
        </head>
        <body class="{BODYCLASS}">
            <header>
                <h1>📁 File Server</h1>
                {TERM_TOGGLE}
            </header>
            <div id="layout">
                <div id="left-panel">
                    <div id="left-scroll">
                        <div id="crumbs">{CRUMBS}</div>

                        <div id="drop-zone" data-path="{CURPATH}">📂 Trascina qui file o cartelle intere</div>
                        <div id="status"></div>
                        <div class="progress"><div id="progress-bar"></div></div>

                        <form method="POST" enctype="multipart/form-data" action="/upload">
                            <input type="hidden" name="path" value="{CURPATH}">
                            <input type="file" name="file" multiple>
                            <input type="submit" value="Carica file">
                        </form>
                        <form method="POST" enctype="multipart/form-data" action="/upload">
                            <input type="hidden" name="path" value="{CURPATH}">
                            <input type="file" name="file" webkitdirectory multiple>
                            <input type="submit" value="Carica cartella">
                        </form>
                        <form id="new-form" autocomplete="off">
                            <input type="text" id="new-name" placeholder="nome file (o sotto/cartella/file.txt)">
                            <button type="submit" id="new-file-btn" title="Crea file nella cartella corrente e aprilo">📄 Nuovo file</button>
                            <button type="button" id="new-dir-btn" title="Crea cartella">📁 Nuova cartella</button>
                            <button type="button" id="refresh-btn">🔄</button>
                        </form>

                        <h2>Contenuti disponibili</h2>
                        <form id="search-form" autocomplete="off">
                            <input type="text" id="search-q" placeholder="🔎 cerca in questa cartella (ricorsivo)">
                            <label><input type="checkbox" id="search-content"> nel contenuto</label>
                            <button type="submit">Cerca</button>
                        </form>
                        <div id="search-results"></div>
                        <div id="tools">
                            <input type="text" id="filter-input" placeholder="filtra per nome">
                            <label><input type="checkbox" id="show-hidden" checked> file nascosti</label>
                        </div>
                        <div id="sel-bar">
                            <span id="sel-count"></span>
                            <button id="sel-zip">⬇️ ZIP</button>
                            <button id="sel-move">➡️ Sposta</button>
                            <button id="sel-copy">⧉ Copia</button>
                            <button id="sel-del">🗑 Elimina</button>
                        </div>
                        <table>
                            <thead><tr><th><input type="checkbox" id="sel-all" title="Seleziona tutto"></th><th data-sort="name">Nome</th><th data-sort="size">Dimensione</th><th data-sort="mtime">Data modifica</th><th>Azioni</th></tr></thead>
                            <tbody>{ROWS}</tbody>
                        </table>
                    </div>
                </div>
                <div id="vsplitter" title="Ridimensiona pannelli"></div>
                <div id="right-panel">
                    {TERM_UI}
                    <div id="editor-panel">
                        <div id="editor-tabs"></div>
                        <div id="editor-bar">
                            <span id="editor-title">Editor</span>
                            <span id="editor-status"></span>
                            <label title="Salva automaticamente 2s dopo l'ultima modifica"><input type="checkbox" id="autosave"> Auto</label>
                            <button id="editor-diff" class="ed-btn" title="Confronta l'editor con il file su disco">⇄ Confronta</button>
                            <button id="editor-preview" class="ed-btn" style="display:none" title="Mostra/nascondi anteprima Markdown">👁 Anteprima</button>
                            <button id="editor-newtab" class="ed-btn" title="Apri il file in un nuovo tab a schermo intero">↗ Nuovo tab</button>
                            <button id="editor-save">💾 Salva</button>
                        </div>
                        <div id="find-bar">
                            <input type="text" id="find-input" placeholder="Cerca">
                            <input type="text" id="repl-input" placeholder="Sostituisci con">
                            <label><input type="checkbox" id="find-case"> Aa</label>
                            <button id="find-prev" title="Precedente (Maiusc+Invio)">◀</button>
                            <button id="find-next" title="Successivo (Invio)">▶</button>
                            <button id="repl-one">Sostituisci</button>
                            <button id="repl-all">Tutti</button>
                            <button id="find-close">✕</button>
                            <span id="find-info"></span>
                        </div>
                        <div id="editor-host">
                            <div id="editor-placeholder">Seleziona un file (✏️ Modifica) o creane uno nuovo (➕ Nuovo file)</div>
                        </div>
                    </div>
                </div>
            </div>

            <script>
                var dropZone = document.getElementById("drop-zone");
                var statusEl = document.getElementById("status");
                var progressWrap = document.querySelector(".progress");
                var progressBar = document.getElementById("progress-bar");

                ["dragenter", "dragover"].forEach(function (evt) {
                    dropZone.addEventListener(evt, function (e) {
                        e.preventDefault();
                        dropZone.classList.add("dragover");
                    });
                });
                ["dragleave", "drop"].forEach(function (evt) {
                    dropZone.addEventListener(evt, function (e) {
                        e.preventDefault();
                        dropZone.classList.remove("dragover");
                    });
                });

                dropZone.addEventListener("drop", function (e) {
                    e.preventDefault();
                    var files = [];
                    var pending = 0;

                    function maybeUpload() {
                        if (pending === 0 && files.length) {
                            uploadFiles(files);
                        }
                    }

                    function collect(entry, path) {
                        path = path || "";
                        if (entry.isFile) {
                            pending++;
                            entry.file(function (file) {
                                files.push({ name: path + file.name, file: file });
                                pending--;
                                maybeUpload();
                            });
                        } else if (entry.isDirectory) {
                            var reader = entry.createReader();
                            (function readEntries() {
                                reader.readEntries(function (entries) {
                                    if (entries.length) {
                                        entries.forEach(function (child) {
                                            collect(child, path + entry.name + "/");
                                        });
                                        readEntries();
                                    }
                                });
                            })();
                        }
                    }

                    var items = e.dataTransfer.items;
                    if (items && items.length) {
                        for (var i = 0; i < items.length; i++) {
                            var entry = items[i].webkitGetAsEntry ? items[i].webkitGetAsEntry() : null;
                            if (entry) {
                                collect(entry);
                            } else if (items[i].getAsFile) {
                                var f = items[i].getAsFile();
                                if (f) {
                                    files.push({ name: f.name, file: f });
                                }
                            }
                        }
                        maybeUpload();
                    } else {
                        for (var j = 0; j < e.dataTransfer.files.length; j++) {
                            files.push({ name: e.dataTransfer.files[j].name, file: e.dataTransfer.files[j] });
                        }
                        uploadFiles(files);
                    }
                });

                function uploadFiles(files) {
                    var formData = new FormData();
                    formData.append("path", dropZone.getAttribute("data-path") || "");
                    files.forEach(function (f) {
                        formData.append("file", f.file, f.name);
                    });

                    statusEl.style.display = "block";
                    progressWrap.style.display = "block";
                    statusEl.textContent = "⏳ Upload in corso di " + files.length + " file...";

                    var xhr = new XMLHttpRequest();
                    xhr.open("POST", "/upload", true);
                    xhr.upload.onprogress = function (e) {
                        if (e.lengthComputable) {
                            progressBar.style.width = Math.round((e.loaded / e.total) * 100) + "%";
                        }
                    };
                    xhr.onload = function () {
                        if (xhr.status === 200 || xhr.status === 303) {
                            statusEl.textContent = uploadResult(xhr);
                            refreshFileList();
                        } else {
                            statusEl.textContent = "❌ Errore upload (HTTP " + xhr.status + ")";
                            progressWrap.style.display = "none";
                        }
                    };
                    xhr.onerror = function () {
                        statusEl.textContent = "❌ Errore di rete durante l'upload";
                        progressWrap.style.display = "none";
                    };
                    xhr.send(formData);
                }
            </script>
            <script src="/static/codemirror/lib/codemirror.min.js"></script>
            <script src="/static/codemirror/mode/shell.min.js"></script>
            <script src="/static/codemirror/mode/python.min.js"></script>
            <script src="/static/codemirror/mode/javascript.min.js"></script>
            <script src="/static/codemirror/mode/markdown.min.js"></script>
            <script src="/static/codemirror/mode/htmlmixed.min.js"></script>
            <script src="/static/codemirror/mode/css.min.js"></script>
            <script src="/static/codemirror/mode/xml.min.js"></script>
            <script src="/static/marked/marked.min.js"></script>
            <script>
                var curPath = document.getElementById("drop-zone").getAttribute("data-path") || "";
                var NL = String.fromCharCode(10);
                var IS_FULL = !!new URLSearchParams(location.search).get("view");
                var editorTabs = document.getElementById("editor-tabs");
                var editorTitle = document.getElementById("editor-title");
                var editorStatus = document.getElementById("editor-status");
                var editorHost = document.getElementById("editor-host");
                var editorSave = document.getElementById("editor-save");
                var cm = null;
                var openTabs = [];
                var activeTab = null;
                var restoring = false;

                // --- Utilità ---
                function xhrJson(method, url, body, cb) {
                    var xhr = new XMLHttpRequest();
                    xhr.open(method, url, true);
                    if (body !== undefined) xhr.setRequestHeader("Content-Type", "application/json");
                    xhr.onload = function () {
                        var o = null;
                        try { o = JSON.parse(xhr.responseText); } catch (e) {}
                        cb(xhr.status, o, xhr);
                    };
                    xhr.onerror = function () { cb(0, null, xhr); };
                    xhr.send(body === undefined ? null : JSON.stringify(body));
                }

                function joinPath(dir, name) {
                    return dir.charAt(dir.length - 1) === "/" ? dir + name : dir + "/" + name;
                }

                function absPath(v) {
                    v = v.trim();
                    return v.charAt(0) === "/" ? v : joinPath(curPath, v);
                }

                function baseName(p) { return p.split("/").filter(Boolean).pop() || p; }

                function editorSetStatus(msg, color) {
                    editorStatus.textContent = msg || "";
                    editorStatus.style.color = color || "#f9a825";
                }

                function flash(msg, color, ms) {
                    editorSetStatus(msg, color);
                    setTimeout(function () { editorSetStatus(""); }, ms || 2500);
                }

                function listMsg(msg) {
                    statusEl.style.display = "block";
                    statusEl.textContent = msg;
                }

                function uploadResult(xhr) {
                    var o = null;
                    try { o = JSON.parse(xhr.responseText); } catch (e) {}
                    var m = "✅ Upload completato!";
                    if (o && o.renamed && o.renamed.length) {
                        m += " Rinominati per non sovrascrivere: " + o.renamed.map(function (r) { return r.from + " → " + r.to; }).join(", ");
                    }
                    return m;
                }

                function pickMode(name) {
                    var ext = (name || "").split(".").pop().toLowerCase();
                    var map = {
                        "py": "python", "sh": "shell", "bash": "shell",
                        "js": "javascript", "mjs": "javascript", "cjs": "javascript",
                        "md": "markdown", "json": "javascript",
                        "html": "htmlmixed", "htm": "htmlmixed",
                        "css": "css", "xml": "xml", "svg": "xml"
                    };
                    return map[ext] || null;
                }

                function refreshEditor() {
                    if (cm) setTimeout(function () { cm.refresh(); }, 10);
                }

                // --- Overlay generico (anteprima file, confronto) ---
                function closeOverlay() {
                    var o = document.getElementById("overlay");
                    if (o) o.remove();
                }

                function openOverlay(title, node) {
                    closeOverlay();
                    var ov = document.createElement("div");
                    ov.id = "overlay";
                    var box = document.createElement("div");
                    box.className = "ov-box";
                    var head = document.createElement("div");
                    head.className = "ov-head";
                    var h = document.createElement("span");
                    h.textContent = title;
                    var x = document.createElement("button");
                    x.textContent = "✕";
                    x.addEventListener("click", closeOverlay);
                    head.appendChild(h);
                    head.appendChild(x);
                    box.appendChild(head);
                    node.classList.add("ov-body");
                    box.appendChild(node);
                    ov.appendChild(box);
                    ov.addEventListener("click", function (e) { if (e.target === ov) closeOverlay(); });
                    document.body.appendChild(ov);
                }

                var PREVIEW_KIND = {
                    png: "img", jpg: "img", jpeg: "img", gif: "img", webp: "img", bmp: "img", ico: "img",
                    pdf: "pdf", mp4: "video", webm: "video", ogv: "video", mp3: "audio", wav: "audio", m4a: "audio", ogg: "audio"
                };

                function showFilePreview(path) {
                    var ext = path.split(".").pop().toLowerCase();
                    var kind = PREVIEW_KIND[ext];
                    if (!kind) return;
                    var url = "/download?inline=1&file=" + encodeURIComponent(path);
                    var node;
                    if (kind === "img") { node = document.createElement("img"); node.src = url; }
                    else if (kind === "pdf") { node = document.createElement("iframe"); node.src = url; }
                    else { node = document.createElement(kind); node.src = url; node.controls = true; node.autoplay = false; }
                    openOverlay(baseName(path), node);
                }

                // --- Tab dell'editor ---
                function findTab(name) {
                    for (var i = 0; i < openTabs.length; i++) {
                        if (openTabs[i].name === name) return openTabs[i];
                    }
                    return null;
                }

                function isDirty(t) { return !t.doc.isClean(t.gen); }

                function persistTabs() {
                    if (IS_FULL || restoring) return;
                    try {
                        localStorage.setItem("fs.tabs", JSON.stringify({
                            tabs: openTabs.map(function (t) { return t.name; }),
                            active: activeTab ? activeTab.name : null
                        }));
                    } catch (e) {}
                }

                function renderTabs() {
                    editorTabs.innerHTML = "";
                    openTabs.forEach(function (t) {
                        var tab = document.createElement("div");
                        tab.className = "ed-tab" + (t === activeTab ? " active" : "") + (isDirty(t) ? " dirty" : "");
                        tab.title = t.name;
                        var label = document.createElement("span");
                        label.className = "ed-label";
                        label.textContent = baseName(t.name);
                        tab.appendChild(label);
                        var close = document.createElement("span");
                        close.className = "ed-tab-close";
                        close.textContent = "✕";
                        close.addEventListener("click", function (e) { e.stopPropagation(); closeTab(t); });
                        tab.appendChild(close);
                        tab.addEventListener("click", function () { activateTab(t); });
                        tab.addEventListener("auxclick", function (e) { if (e.button === 1) closeTab(t); });
                        editorTabs.appendChild(tab);
                    });
                }

                // --- Autosave ---
                var autosaveBox = document.getElementById("autosave");
                var autosaveTimer = null;
                try { autosaveBox.checked = localStorage.getItem("fs.autosave") === "1"; } catch (e) {}
                autosaveBox.addEventListener("change", function () {
                    try { localStorage.setItem("fs.autosave", autosaveBox.checked ? "1" : "0"); } catch (e) {}
                    scheduleAutosave();
                });

                function scheduleAutosave() {
                    if (!autosaveBox.checked || !activeTab) return;
                    clearTimeout(autosaveTimer);
                    var t = activeTab;
                    autosaveTimer = setTimeout(function () { if (isDirty(t)) saveTab(t, { auto: true }); }, 2000);
                }

                function saveTab(t, opts) {
                    opts = opts || {};
                    if (t.saving) return;
                    t.saving = true;
                    var gen = t.doc.changeGeneration();
                    if (t === activeTab) editorSetStatus("⏳ Salvataggio...");
                    var body = { path: t.name, content: t.doc.getValue(), mtime: t.mtime };
                    if (opts.force) body.force = true;
                    xhrJson("POST", "/save", body, function (st, o) {
                        t.saving = false;
                        if (st === 200) {
                            t.gen = gen;
                            t.mtime = o.mtime;
                            renderTabs();
                            if (t === activeTab) flash(opts.auto ? "✅ Salvato (auto)" : "✅ Salvato");
                            refreshFileList();
                            if (isDirty(t)) scheduleAutosave();
                        } else if (st === 409) {
                            if (opts.auto) {
                                if (t === activeTab) editorSetStatus("⚠️ File cambiato su disco: salvataggio automatico sospeso, salva a mano", "#ef5350");
                            } else if (confirm("«" + baseName(t.name) + "» è stato modificato su disco dopo l'apertura. Sovrascrivere con la versione dell'editor?")) {
                                saveTab(t, { force: true });
                            } else if (t === activeTab) {
                                editorSetStatus("");
                            }
                        } else if (t === activeTab) {
                            editorSetStatus(st === 0 ? "❌ Errore di rete" : "❌ Errore salvataggio (HTTP " + st + ")", "#ef5350");
                        }
                    });
                }

                editorSave.addEventListener("click", function () { if (activeTab) saveTab(activeTab); });

                // --- Editor CodeMirror ---
                function ensureCm() {
                    if (cm) return;
                    editorHost.innerHTML = "";
                    cm = CodeMirror(editorHost, {
                        lineNumbers: true,
                        matchBrackets: true,
                        indentUnit: 4,
                        lineWrapping: true
                    });
                    cm.on("change", function () {
                        if (!activeTab) return;
                        renderTabs();
                        if (previewOn) renderPreview();
                        scheduleAutosave();
                    });
                }

                // --- Anteprima live Markdown / HTML / SVG (iframe sandbox senza script) ---
                var previewOn = false;
                var pvBtn = document.getElementById("editor-preview");
                var MD_CSS = "body{font-family:sans-serif;max-width:900px;margin:0 auto;padding:16px 24px;line-height:1.55;color:#222}" +
                    "pre{background:#f5f5f5;padding:10px;overflow:auto;border-radius:6px}code{background:#f5f5f5;padding:1px 4px;border-radius:3px}pre code{padding:0}" +
                    "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 10px}blockquote{border-left:4px solid #ccc;margin-left:0;padding-left:14px;color:#555}img{max-width:100%}";

                function previewKind(name) {
                    var ext = (name || "").split(".").pop().toLowerCase();
                    if (ext === "md" || ext === "markdown") return "md";
                    if (ext === "html" || ext === "htm" || ext === "svg") return "html";
                    return null;
                }

                function renderPreview() {
                    var fr = document.getElementById("md-preview");
                    if (!fr || !activeTab) return;
                    var src = activeTab.doc.getValue();
                    if (previewKind(activeTab.name) === "md") {
                        var body = window.marked ? marked.parse(src) : "<pre>marked non disponibile</pre>";
                        fr.srcdoc = "<!DOCTYPE html><meta charset='utf-8'><style>" + MD_CSS + "</style>" + body;
                    } else {
                        fr.srcdoc = src;
                    }
                }

                function setPreview(on) {
                    previewOn = on && !!activeTab && !!previewKind(activeTab.name);
                    var fr = document.getElementById("md-preview");
                    if (previewOn) {
                        if (!fr) {
                            fr = document.createElement("iframe");
                            fr.id = "md-preview";
                            fr.setAttribute("sandbox", "");
                            fr.style.cssText = "position:absolute;inset:0;width:100%;height:100%;border:0;background:#fff";
                            editorHost.appendChild(fr);
                        }
                        renderPreview();
                    } else if (fr) {
                        fr.remove();
                    }
                    pvBtn.textContent = previewOn ? "✏️ Modifica" : "👁 Anteprima";
                    if (!previewOn && cm) { cm.refresh(); cm.focus(); }
                }

                pvBtn.addEventListener("click", function () { setPreview(!previewOn); });

                function showPlaceholder() {
                    editorTitle.textContent = "Editor";
                    editorSetStatus("");
                    if (cm) { var we = cm.getWrapperElement(); if (we.parentNode) we.parentNode.removeChild(we); cm = null; }
                    previewOn = false;
                    pvBtn.style.display = "none";
                    closeFindBar();
                    editorHost.innerHTML = "<div id='editor-placeholder'>Clicca su un file per aprirlo nell'editor, o creane uno nuovo (📄 Nuovo file)</div>";
                }

                function activateTab(t) {
                    activeTab = t;
                    ensureCm();
                    cm.swapDoc(t.doc);
                    setPreview(false);
                    pvBtn.style.display = previewKind(t.name) ? "" : "none";
                    editorTitle.textContent = "✏️ " + t.name;
                    editorSetStatus("");
                    renderTabs();
                    persistTabs();
                    setTimeout(function () { cm.refresh(); cm.focus(); }, 10);
                }

                function openEditor(name, content, mtime) {
                    var existing = findTab(name);
                    if (existing) { activateTab(existing); return; }
                    var mode = pickMode(name);
                    if (!(window.CodeMirror && CodeMirror.modes && mode && CodeMirror.modes[mode])) mode = null;
                    var doc = CodeMirror.Doc(content, mode || "text/plain");
                    var t = { name: name, doc: doc, gen: doc.changeGeneration(), mtime: mtime };
                    openTabs.push(t);
                    activateTab(t);
                }

                function closeTab(t, force) {
                    if (!force && isDirty(t) && !confirm("«" + baseName(t.name) + "» ha modifiche non salvate. Chiudere comunque?")) return;
                    var idx = openTabs.indexOf(t);
                    if (idx === -1) return;
                    openTabs.splice(idx, 1);
                    if (t === activeTab) {
                        activeTab = null;
                        if (openTabs.length) activateTab(openTabs[Math.min(idx, openTabs.length - 1)]);
                        else showPlaceholder();
                    }
                    renderTabs();
                    persistTabs();
                }

                function closeTabsUnder(path) {
                    openTabs.slice().forEach(function (t) {
                        if (t.name === path || t.name.indexOf(path + "/") === 0) closeTab(t, true);
                    });
                }

                function renameTabs(oldPath, newPath) {
                    openTabs.forEach(function (t) {
                        if (t.name === oldPath || t.name.indexOf(oldPath + "/") === 0) {
                            t.name = newPath + t.name.slice(oldPath.length);
                            if (t === activeTab) editorTitle.textContent = "✏️ " + t.name;
                        }
                    });
                    renderTabs();
                    persistTabs();
                }

                window.addEventListener("beforeunload", function (e) {
                    if (openTabs.some(isDirty)) { e.preventDefault(); e.returnValue = ""; }
                });

                function openFile(file, done, fail) {
                    xhrJson("GET", "/edit?file=" + encodeURIComponent(file), undefined, function (st, o) {
                        if (st === 200 && o) {
                            openEditor(o.name, o.content, o.mtime);
                            if (done) done(o);
                        } else if (fail) {
                            fail(st);
                        } else {
                            alert(st === 0 ? "Errore di rete" : "Impossibile aprire il file (HTTP " + st + ")");
                        }
                    });
                }

                document.addEventListener("click", function (e) {
                    var link = e.target.closest ? e.target.closest(".edit-link") : null;
                    if (!link) return;
                    e.preventDefault();
                    openFile(link.getAttribute("data-file"));
                });

                // --- Cerca / sostituisci ---
                var findBar = document.getElementById("find-bar");
                var findInput = document.getElementById("find-input");
                var replInput = document.getElementById("repl-input");
                var findCase = document.getElementById("find-case");
                var findInfo = document.getElementById("find-info");

                function closeFindBar() {
                    findBar.style.display = "none";
                    findInfo.textContent = "";
                }

                function openFindBar() {
                    if (!cm) return;
                    findBar.style.display = "flex";
                    var sel = cm.getSelection();
                    if (sel && sel.indexOf(NL) === -1) findInput.value = sel;
                    findInput.focus();
                    findInput.select();
                    refreshEditor();
                }

                function fbNeedle() { return findCase.checked ? findInput.value : findInput.value.toLowerCase(); }
                function fbText() { var v = cm.getValue(); return findCase.checked ? v : v.toLowerCase(); }

                function findStep(dir) {
                    if (!cm || !findInput.value) return false;
                    var text = fbText(), q = fbNeedle(), idx;
                    if (dir > 0) {
                        idx = text.indexOf(q, cm.indexFromPos(cm.getCursor("to")));
                        if (idx < 0) idx = text.indexOf(q, 0);
                    } else {
                        var from = cm.indexFromPos(cm.getCursor("from")) - 1;
                        idx = from < 0 ? -1 : text.lastIndexOf(q, from);
                        if (idx < 0) idx = text.lastIndexOf(q);
                    }
                    if (idx < 0) { findInfo.textContent = "Nessun risultato"; return false; }
                    findInfo.textContent = "";
                    cm.setSelection(cm.posFromIndex(idx), cm.posFromIndex(idx + q.length));
                    cm.scrollIntoView(cm.getCursor("from"), 100);
                    return true;
                }

                function replaceOne() {
                    if (!cm || !findInput.value) return;
                    var sel = cm.getSelection();
                    if (sel && (findCase.checked ? sel : sel.toLowerCase()) === fbNeedle()) {
                        cm.replaceSelection(replInput.value);
                    }
                    findStep(1);
                }

                function replaceAll() {
                    if (!cm || !findInput.value) return;
                    var text = fbText(), q = fbNeedle(), idxs = [], i = 0;
                    while ((i = text.indexOf(q, i)) !== -1) { idxs.push(i); i += q.length; }
                    cm.operation(function () {
                        for (var k = idxs.length - 1; k >= 0; k--) {
                            cm.replaceRange(replInput.value, cm.posFromIndex(idxs[k]), cm.posFromIndex(idxs[k] + q.length));
                        }
                    });
                    findInfo.textContent = idxs.length + " sostituzioni";
                }

                document.getElementById("find-next").addEventListener("click", function () { findStep(1); });
                document.getElementById("find-prev").addEventListener("click", function () { findStep(-1); });
                document.getElementById("repl-one").addEventListener("click", replaceOne);
                document.getElementById("repl-all").addEventListener("click", replaceAll);
                document.getElementById("find-close").addEventListener("click", function () { closeFindBar(); if (cm) cm.focus(); });
                findInput.addEventListener("keydown", function (e) {
                    if (e.key === "Enter") { e.preventDefault(); findStep(e.shiftKey ? -1 : 1); }
                    else if (e.key === "Escape") { closeFindBar(); if (cm) cm.focus(); }
                });
                replInput.addEventListener("keydown", function (e) {
                    if (e.key === "Enter") { e.preventDefault(); replaceOne(); }
                    else if (e.key === "Escape") { closeFindBar(); if (cm) cm.focus(); }
                });

                document.addEventListener("keydown", function (e) {
                    if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")) {
                        e.preventDefault();
                        editorSave.click();
                    } else if ((e.ctrlKey || e.metaKey) && (e.key === "f" || e.key === "F") && activeTab && !previewOn &&
                               e.target.closest && e.target.closest("#editor-panel")) {
                        e.preventDefault();
                        openFindBar();
                    } else if (e.key === "Escape") {
                        closeOverlay();
                    }
                });

                // --- Confronto con la versione su disco ---
                function diffLines(a, b) {
                    var i = 0;
                    while (i < a.length && i < b.length && a[i] === b[i]) i++;
                    var ea = a.length, eb = b.length;
                    while (ea > i && eb > i && a[ea - 1] === b[eb - 1]) { ea--; eb--; }
                    var A = a.slice(i, ea), B = b.slice(i, eb), ops = [], k;
                    for (k = 0; k < i; k++) ops.push([" ", a[k]]);
                    if (A.length * B.length > 4000000) {
                        A.forEach(function (l) { ops.push(["-", l]); });
                        B.forEach(function (l) { ops.push(["+", l]); });
                    } else {
                        var n = A.length, m = B.length, L = [], r, c;
                        for (r = 0; r <= n; r++) L.push(new Int32Array(m + 1));
                        for (r = n - 1; r >= 0; r--) {
                            for (c = m - 1; c >= 0; c--) {
                                L[r][c] = A[r] === B[c] ? L[r + 1][c + 1] + 1 : Math.max(L[r + 1][c], L[r][c + 1]);
                            }
                        }
                        r = 0; c = 0;
                        while (r < n && c < m) {
                            if (A[r] === B[c]) { ops.push([" ", A[r]]); r++; c++; }
                            else if (L[r + 1][c] >= L[r][c + 1]) { ops.push(["-", A[r]]); r++; }
                            else { ops.push(["+", B[c]]); c++; }
                        }
                        while (r < n) { ops.push(["-", A[r++]]); }
                        while (c < m) { ops.push(["+", B[c++]]); }
                    }
                    for (k = ea; k < a.length; k++) ops.push([" ", a[k]]);
                    return ops;
                }

                function showDiff() {
                    if (!activeTab) return;
                    var t = activeTab;
                    xhrJson("GET", "/edit?file=" + encodeURIComponent(t.name), undefined, function (st, o) {
                        if (st !== 200 || !o) { flash("❌ Impossibile leggere il file su disco", "#ef5350"); return; }
                        var ops = diffLines(o.content.split(NL), t.doc.getValue().split(NL));
                        var changed = ops.some(function (x) { return x[0] !== " "; });
                        var pre = document.createElement("pre");
                        pre.className = "diff";
                        if (!changed) {
                            pre.textContent = "Nessuna differenza: l'editor coincide con il file su disco.";
                        } else {
                            var near = ops.map(function () { return false; });
                            ops.forEach(function (x, idx) {
                                if (x[0] !== " ") for (var d = -3; d <= 3; d++) if (near[idx + d] !== undefined) near[idx + d] = true;
                            });
                            var skipped = false;
                            ops.forEach(function (x, idx) {
                                if (!near[idx]) {
                                    if (!skipped) { var s = document.createElement("span"); s.className = "dctx"; s.textContent = "…" + NL; pre.appendChild(s); skipped = true; }
                                    return;
                                }
                                skipped = false;
                                var line = document.createElement("span");
                                line.className = x[0] === "+" ? "dadd" : (x[0] === "-" ? "ddel" : "dctx");
                                line.textContent = x[0] + " " + x[1] + NL;
                                pre.appendChild(line);
                            });
                        }
                        openOverlay("Disco (−) vs Editor (+): " + baseName(t.name), pre);
                    });
                }

                document.getElementById("editor-diff").addEventListener("click", showDiff);

                // --- Navigazione elenco file (con cronologia del browser) ---
                var filterInput = document.getElementById("filter-input");
                var hiddenBox = document.getElementById("show-hidden");
                var sortKey = "name", sortDir = 1;
                try { hiddenBox.checked = localStorage.getItem("fs.hidden") !== "0"; } catch (e) {}

                function applyView() {
                    var tb = document.querySelector("table tbody");
                    var rows = [].slice.call(tb.querySelectorAll("tr[data-name]"));
                    var fq = filterInput.value.trim().toLowerCase(), showHidden = hiddenBox.checked;
                    rows.sort(function (a, b) {
                        var da = a.dataset.dir === "1", db = b.dataset.dir === "1";
                        if (da !== db) return da ? -1 : 1;
                        var r = sortKey === "name"
                            ? a.dataset.name.toLowerCase().localeCompare(b.dataset.name.toLowerCase())
                            : (+a.dataset[sortKey]) - (+b.dataset[sortKey]);
                        return r * sortDir;
                    });
                    rows.forEach(function (r) {
                        tb.appendChild(r);
                        var n = r.dataset.name;
                        var hide = (!showHidden && n.charAt(0) === ".") || (fq && n.toLowerCase().indexOf(fq) < 0);
                        r.style.display = hide ? "none" : "";
                    });
                    document.querySelectorAll("th[data-sort]").forEach(function (th) {
                        var base = th.getAttribute("data-label");
                        th.textContent = base + (th.getAttribute("data-sort") === sortKey ? (sortDir > 0 ? " ▲" : " ▼") : "");
                    });
                    updateSelBar();
                }

                document.querySelectorAll("th[data-sort]").forEach(function (th) {
                    th.setAttribute("data-label", th.textContent);
                    th.addEventListener("click", function () {
                        var k = th.getAttribute("data-sort");
                        if (k === sortKey) sortDir = -sortDir; else { sortKey = k; sortDir = 1; }
                        applyView();
                    });
                });
                filterInput.addEventListener("input", applyView);
                hiddenBox.addEventListener("change", function () {
                    try { localStorage.setItem("fs.hidden", hiddenBox.checked ? "1" : "0"); } catch (e) {}
                    applyView();
                });

                // mode: undefined = nuova navigazione (cronologia), "refresh" = ricarica elenco, "pop" = back/forward
                function navigateTo(path, mode) {
                    var xhr = new XMLHttpRequest();
                    xhr.open("GET", "/?path=" + encodeURIComponent(path || ""), true);
                    xhr.onload = function () {
                        if (xhr.status === 200) {
                            var doc = new DOMParser().parseFromString(xhr.responseText, "text/html");
                            var dz = doc.getElementById("drop-zone");
                            curPath = dz.getAttribute("data-path") || "";
                            document.getElementById("drop-zone").setAttribute("data-path", curPath);
                            document.querySelectorAll("input[name=path]").forEach(function (i) { i.value = curPath; });
                            document.querySelector("table tbody").innerHTML = doc.querySelector("table tbody").innerHTML;
                            document.querySelector("#crumbs").innerHTML = doc.querySelector("#crumbs").innerHTML;
                            document.title = "File Server - " + curPath;
                            if (mode !== "refresh") {
                                filterInput.value = "";
                                closeSearch();
                            }
                            if (!mode && !IS_FULL && !(history.state && history.state.path === curPath)) {
                                history.pushState({ path: curPath }, "", "/?path=" + encodeURIComponent(curPath));
                            }
                            document.getElementById("sel-all").checked = false;
                            applyView();
                        } else if (mode !== "refresh") {
                            alert("Impossibile aprire la cartella (HTTP " + xhr.status + ")");
                        }
                    };
                    xhr.send();
                }

                function refreshFileList() {
                    navigateTo(curPath, "refresh");
                }

                if (!IS_FULL) {
                    history.replaceState({ path: curPath }, "");
                    window.addEventListener("popstate", function (e) {
                        if (e.state && e.state.path !== undefined) navigateTo(e.state.path, "pop");
                    });
                }

                document.addEventListener("click", function (e) {
                    var link = e.target.closest ? e.target.closest(".nav-link") : null;
                    if (!link) return;
                    e.preventDefault();
                    navigateTo(link.getAttribute("data-path") || "");
                });

                // --- Selezione multipla e azioni sull'elenco ---
                var selBar = document.getElementById("sel-bar");
                var selCount = document.getElementById("sel-count");

                function selectedPaths() {
                    return [].slice.call(document.querySelectorAll("input.sel:checked")).map(function (c) { return c.getAttribute("data-path"); });
                }

                function updateSelBar() {
                    var n = selectedPaths().length;
                    selBar.style.display = n ? "flex" : "none";
                    selCount.textContent = n + " selezionati";
                }

                document.addEventListener("change", function (e) {
                    if (e.target.id === "sel-all") {
                        document.querySelectorAll("tbody tr").forEach(function (r) {
                            var c = r.querySelector("input.sel");
                            if (c && r.style.display !== "none") c.checked = e.target.checked;
                        });
                        updateSelBar();
                    } else if (e.target.classList && e.target.classList.contains("sel")) {
                        updateSelBar();
                    }
                });

                function runSeq(items, fn, done) {
                    var i = 0, errors = [];
                    (function next() {
                        if (i >= items.length) { done(errors); return; }
                        var it = items[i++];
                        fn(it, function (ok) { if (!ok) errors.push(it); next(); });
                    })();
                }

                function deleteFile(path, label) {
                    if (!confirm("Confermi cancellazione " + label + "?")) return;
                    xhrJson("POST", "/delete-multi", { paths: [path] }, function (st, o) {
                        if (st !== 200 || (o && o.errors.length)) alert("Errore cancellazione");
                        else closeTabsUnder(path);
                        refreshFileList();
                    });
                }

                document.getElementById("sel-del").addEventListener("click", function () {
                    var paths = selectedPaths();
                    if (!paths.length || !confirm("Eliminare " + paths.length + " elementi selezionati?")) return;
                    xhrJson("POST", "/delete-multi", { paths: paths }, function (st, o) {
                        if (st === 200 && o) {
                            o.deleted.forEach(closeTabsUnder);
                            if (o.errors.length) alert("Non eliminati: " + o.errors.join(", "));
                        } else {
                            alert("Errore cancellazione");
                        }
                        refreshFileList();
                    });
                });

                document.getElementById("sel-zip").addEventListener("click", function () {
                    var paths = selectedPaths();
                    if (!paths.length) return;
                    window.location = "/download-multi?" + paths.map(function (p) { return "p=" + encodeURIComponent(p); }).join("&");
                });

                function moveCopySelection(op) {
                    var paths = selectedPaths();
                    if (!paths.length) return;
                    var v = prompt((op === "rename" ? "Sposta" : "Copia") + " " + paths.length + " elementi nella cartella:", curPath);
                    if (!v) return;
                    var dest = absPath(v);
                    xhrJson("POST", "/new", { path: dest, type: "dir" }, function () {
                        runSeq(paths, function (p, next) {
                            xhrJson("POST", "/" + op, { src: p, dst: dest }, function (st, o) {
                                if (st === 200 && op === "rename") renameTabs(p, o.path);
                                next(st === 200);
                            });
                        }, function (errors) {
                            if (errors.length) alert("Non completati (esistono già o errore): " + errors.map(baseName).join(", "));
                            refreshFileList();
                        });
                    });
                }
                document.getElementById("sel-move").addEventListener("click", function () { moveCopySelection("rename"); });
                document.getElementById("sel-copy").addEventListener("click", function () { moveCopySelection("copy"); });

                document.addEventListener("click", function (e) {
                    var link = e.target.closest ? e.target.closest(".del-link, .ren-link, .cp-link, .ext-link, .prev-link") : null;
                    if (!link) return;
                    e.preventDefault();
                    var path = link.getAttribute("data-path");
                    var cls = link.className;
                    if (cls.indexOf("del-link") !== -1) {
                        deleteFile(path, link.getAttribute("data-label"));
                    } else if (cls.indexOf("prev-link") !== -1) {
                        showFilePreview(path);
                    } else if (cls.indexOf("ren-link") !== -1) {
                        var v = prompt("Rinomina/sposta in (nome o percorso):", baseName(path));
                        if (!v) return;
                        var dst = absPath(v);
                        if (dst === path) return;
                        xhrJson("POST", "/rename", { src: path, dst: dst }, function (st, o) {
                            if (st === 200) renameTabs(path, o.path);
                            else alert(st === 409 ? "Esiste già un elemento con questo nome" : "Errore (HTTP " + st + ")");
                            refreshFileList();
                        });
                    } else if (cls.indexOf("cp-link") !== -1) {
                        var c = prompt("Copia come (nome o percorso):", baseName(path) + " (copia)");
                        if (!c) return;
                        xhrJson("POST", "/copy", { src: path, dst: absPath(c) }, function (st) {
                            if (st !== 200) alert(st === 409 ? "Esiste già un elemento con questo nome" : "Errore (HTTP " + st + ")");
                            refreshFileList();
                        });
                    } else if (cls.indexOf("ext-link") !== -1) {
                        listMsg("⏳ Estrazione in corso...");
                        xhrJson("POST", "/extract", { path: path }, function (st, o) {
                            listMsg(st === 200 ? "✅ Estratti " + o.files + " file in " + baseName(o.path) : "❌ Estrazione non riuscita (HTTP " + st + ")");
                            refreshFileList();
                        });
                    }
                });

                // --- Ricerca ricorsiva ---
                var searchBox = document.getElementById("search-results");

                function closeSearch() {
                    searchBox.style.display = "none";
                    searchBox.innerHTML = "";
                }

                document.getElementById("search-form").addEventListener("submit", function (e) {
                    e.preventDefault();
                    var q = document.getElementById("search-q").value.trim();
                    if (!q) return;
                    var byContent = document.getElementById("search-content").checked;
                    searchBox.style.display = "block";
                    searchBox.textContent = "⏳ Ricerca in corso...";
                    xhrJson("GET", "/search?path=" + encodeURIComponent(curPath) + "&q=" + encodeURIComponent(q) + "&content=" + (byContent ? "1" : "0"), undefined, function (st, o) {
                        searchBox.innerHTML = "";
                        if (st !== 200 || !o) { searchBox.textContent = "❌ Errore ricerca (HTTP " + st + ")"; return; }
                        var head = document.createElement("div");
                        head.className = "sr-head";
                        head.textContent = o.results.length + " risultati" + (o.truncated ? " (parziali: limite raggiunto)" : "") + " ";
                        var x = document.createElement("a");
                        x.href = "#";
                        x.textContent = "[chiudi]";
                        x.addEventListener("click", function (ev) { ev.preventDefault(); closeSearch(); });
                        head.appendChild(x);
                        searchBox.appendChild(head);
                        o.results.forEach(function (r) {
                            var it = document.createElement("div");
                            it.className = "sr-item";
                            var rel = r.path.indexOf(curPath) === 0 ? r.path.slice(curPath.length).replace(/^[/]+/, "") : r.path;
                            it.textContent = (r.type === "dir" ? "📁 " : "📄 ") + rel + (r.line ? ":" + r.line : "");
                            if (r.snippet) {
                                var sn = document.createElement("div");
                                sn.className = "sr-snip";
                                sn.textContent = r.snippet;
                                it.appendChild(sn);
                            }
                            it.addEventListener("click", function () {
                                if (r.type === "dir") { navigateTo(r.path); return; }
                                openFile(r.path, function () {
                                    if (r.line && cm) { cm.setCursor(r.line - 1, 0); cm.scrollIntoView({ line: r.line - 1, ch: 0 }, 150); }
                                }, function () {
                                    window.open("/download?file=" + encodeURIComponent(r.path), "_blank");
                                });
                            });
                            searchBox.appendChild(it);
                        });
                    });
                });

                // --- Creazione nuovo file/cartella ---
                function createEntry(kind) {
                    var input = document.getElementById("new-name");
                    var name = input.value.trim();
                    if (!name) { input.focus(); return; }
                    xhrJson("POST", "/new", { path: absPath(name), type: kind }, function (st, resp) {
                        if (st === 200) {
                            input.value = "";
                            refreshFileList();
                            if (kind === "file") openEditor(resp.path, "", resp.mtime);
                        } else {
                            flash("❌ " + (st === 409 ? "Esiste già" : "Errore creazione (HTTP " + st + ")"), "#ef5350", 3000);
                        }
                    });
                }

                document.querySelectorAll("form[action='/upload']").forEach(function (f) {
                    f.addEventListener("submit", function (e) {
                        e.preventDefault();
                        var fd = new FormData(f);
                        listMsg("⏳ Caricamento...");
                        var xhr = new XMLHttpRequest();
                        xhr.open("POST", "/upload", true);
                        xhr.onload = function () {
                            listMsg(xhr.status === 200 ? uploadResult(xhr) : "❌ Errore (HTTP " + xhr.status + ")");
                            if (xhr.status === 200) refreshFileList();
                        };
                        xhr.send(fd);
                    });
                });

                document.getElementById("new-form").addEventListener("submit", function (e) {
                    e.preventDefault();
                    createEntry("file");
                });
                document.getElementById("new-dir-btn").addEventListener("click", function () {
                    createEntry("dir");
                });

                document.getElementById("refresh-btn").addEventListener("click", function () {
                    refreshFileList();
                });

                // --- Ripristino dei tab aperti nella sessione precedente ---
                (function restoreTabs() {
                    if (IS_FULL) return;
                    var saved = null;
                    try { saved = JSON.parse(localStorage.getItem("fs.tabs") || "null"); } catch (e) {}
                    if (!saved || !saved.tabs || !saved.tabs.length) return;
                    restoring = true;
                    runSeq(saved.tabs, function (name, next) {
                        openFile(name, function () { next(true); }, function () { next(false); });
                    }, function () {
                        restoring = false;
                        var a = saved.active && findTab(saved.active);
                        if (a) activateTab(a);
                        persistTabs();
                    });
                })();

                applyView();

                // --- Splitter ridimensionabili ---
                var leftPanel = document.getElementById("left-panel");
                var vsplitter = document.getElementById("vsplitter");
                var termWrap = document.getElementById("term-wrap");
                var hsplitter = document.getElementById("hsplitter");

                function refreshFit() {
                    refreshEditor();
                    if (typeof fitTerm === "function" && activeTerm) fitTerm(activeTerm);
                }

                try {
                    var savedW = parseInt(localStorage.getItem("fs.leftwidth"), 10);
                    if (savedW > 260) leftPanel.style.width = savedW + "px";
                    var savedH = parseInt(localStorage.getItem("fs.termheight"), 10);
                    if (termWrap && savedH > 100) termWrap.style.height = savedH + "px";
                } catch (e) {}

                vsplitter.addEventListener("mousedown", function (e) {
                    e.preventDefault();
                    var startX = e.clientX;
                    var startW = leftPanel.offsetWidth;
                    function onMove(ev) {
                        var w = startW + (ev.clientX - startX);
                        if (w < 260) w = 260;
                        if (w > window.innerWidth - 300) w = window.innerWidth - 300;
                        leftPanel.style.width = w + "px";
                    }
                    function onUp() {
                        document.removeEventListener("mousemove", onMove);
                        document.removeEventListener("mouseup", onUp);
                        document.body.style.cursor = "";
                        document.body.style.userSelect = "";
                        try { localStorage.setItem("fs.leftwidth", leftPanel.offsetWidth); } catch (e) {}
                        refreshFit();
                    }
                    document.addEventListener("mousemove", onMove);
                    document.addEventListener("mouseup", onUp);
                    document.body.style.cursor = "col-resize";
                    document.body.style.userSelect = "none";
                });

                if (hsplitter) hsplitter.addEventListener("mousedown", function (e) {
                    e.preventDefault();
                    var startY = e.clientY;
                    var startH = termWrap.offsetHeight;
                    function onMove(ev) {
                        var h = startH + (ev.clientY - startY);
                        if (h < 100) h = 100;
                        termWrap.style.height = h + "px";
                    }
                    function onUp() {
                        document.removeEventListener("mousemove", onMove);
                        document.removeEventListener("mouseup", onUp);
                        document.body.style.cursor = "";
                        document.body.style.userSelect = "";
                        try { localStorage.setItem("fs.termheight", termWrap.offsetHeight); } catch (e) {}
                        refreshFit();
                    }
                    document.addEventListener("mousemove", onMove);
                    document.addEventListener("mouseup", onUp);
                    document.body.style.cursor = "row-resize";
                    document.body.style.userSelect = "none";
                });
            </script>
            {TERM_SCRIPTS}
            <script>
                var FULL_VIEW = {FULLVIEW};
                var FULL_FILE = {FULLFILE};
                document.getElementById("editor-newtab").addEventListener("click", function () {
                    if (!activeTab) return;
                    if (isDirty(activeTab) && !confirm("Il file ha modifiche non salvate: il nuovo tab mostrerà la versione su disco. Continuare?")) return;
                    window.open("/?view=editor&file=" + encodeURIComponent(activeTab.name), "_blank");
                });
                var ntBtn = document.getElementById("term-newtab");
                if (ntBtn) ntBtn.addEventListener("click", function () {
                    var sids = (typeof terms !== "undefined" ? terms : []).filter(function (t) { return t.sid; })
                        .sort(function (a, b) { return (b === activeTerm) - (a === activeTerm); })
                        .map(function (t) { return t.sid; });
                    window.open("/?view=term&path=" + encodeURIComponent(curPath) + (sids.length ? "&sid=" + sids.join(",") : ""), "_blank");
                });
                if (FULL_VIEW === "term" && typeof openTerm === "function") {
                    document.title = "Terminale - " + curPath;
                    document.getElementById("term-wrap").classList.add("active");
                    var qs = new URLSearchParams(location.search).get("sid");
                    var sidList = qs ? qs.split(",").filter(Boolean) : [];
                    if (sidList.length) sidList.slice().reverse().forEach(function (sid) { openTerm(sid); });
                    else openTerm();
                } else if (FULL_VIEW === "editor" && FULL_FILE) {
                    document.title = "Editor - " + FULL_FILE;
                    openFile(FULL_FILE);
                }
            </script>
        </body>
        </html>
        """
        TERM_UI = """<div id="term-wrap">
            <div id="term-bar">
                <div id="term-tabs"></div>
                <button id="term-add" title="Nuovo terminale">＋</button>
            </div>
            <div id="term-container"></div>
        </div>
        <div id="hsplitter" title="Ridimensiona terminale"></div>"""

        TERM_TOGGLE = """<div id="header-btns"><button id="term-newtab" title="Apri il terminale in un nuovo tab a schermo intero">↗ Terminale a tutto schermo</button><button id="term-toggle">🖥️ Apri terminale</button></div>"""

        TERM_SCRIPTS = """<script src="/static/xterm/xterm.js"></script>
        <script src="/static/xterm/addon-fit.js"></script>
        <script>
                var termBtn = document.getElementById("term-toggle");
                var termBar = document.getElementById("term-bar");
                var termTabs = document.getElementById("term-tabs");
                var termContainer = document.getElementById("term-container");
                var termAddBtn = document.getElementById("term-add");
                var terms = [];
                var pendingTerms = [];
                var activeTerm = null;
                var termCounter = 0;
                var globalWs = null;
                var wsLock = false;

                function wsConnect(cb) {
                    if (globalWs && globalWs.readyState === WebSocket.OPEN) { cb(globalWs); return; }
                    if (wsLock) { var wait = setInterval(function() { if (!wsLock) { clearInterval(wait); cb(globalWs); } }, 20); return; }
                    wsLock = true;
                    var proto = location.protocol === "https:" ? "wss:" : "ws:";
                    var ws = new WebSocket(proto + "//" + location.host + "/term/ws");
                    ws.binaryType = "arraybuffer";
                    ws.onopen = function () {
                        globalWs = ws;
                        wsLock = false;
                        ws.onmessage = function (evt) {
                            var msg;
                            try { msg = JSON.parse(evt.data); } catch (e) { return; }
                            if (msg.type === "new") {
                                for (var i = 0; i < pendingTerms.length; i++) {
                                    if (!pendingTerms[i].sid) {
                                        pendingTerms[i].sid = msg.sid;
                                        pendingTerms.splice(i, 1);
                                        termFocus();
                                        break;
                                    }
                                }
                            } else if (msg.type === "output") {
                                var t = findTermBySid(msg.sid);
                                if (t && t.term) {
                                    var bin = atob(msg.data);
                                    var bytes = new Uint8Array(bin.length);
                                    for (var j = 0; j < bin.length; j++) bytes[j] = bin.charCodeAt(j);
                                    t.term.write(bytes);
                                }
                            } else if (msg.type === "exit") {
                                var t = findTermBySid(msg.sid);
                                if (t) closeTerm(t);
                            }
                        };
                        cb(ws);
                    };
                    ws.onclose = function () { globalWs = null; wsLock = false; };
                    ws.onerror = function () { globalWs = null; wsLock = false; };
                }

                function findTermBySid(sid) {
                    for (var i = 0; i < terms.length; i++) {
                        if (terms[i].sid === sid) return terms[i];
                    }
                    return null;
                }

                termBtn.addEventListener("click", function () {
                    var wrap = document.getElementById("term-wrap");
                    var split = document.getElementById("hsplitter");
                    // Nasconde/mostra il pannello senza uccidere le sessioni aperte
                    if (wrap.classList.contains("active")) {
                        hideTermPanel();
                    } else {
                        if (!wrap.style.height) wrap.style.height = "320px";
                        wrap.classList.add("active");
                        split.classList.add("active");
                        if (!terms.length) openTerm();
                        updateTermToggle();
                        if (activeTerm) fitTerm(activeTerm);
                        termFocus();
                        refreshEditor();
                    }
                });

                function hideTermPanel() {
                    document.getElementById("term-wrap").classList.remove("active");
                    document.getElementById("hsplitter").classList.remove("active");
                    updateTermToggle();
                    refreshEditor();
                }

                termAddBtn.addEventListener("click", openTerm);

                function openTerm(attachSid) {
                    if (typeof attachSid !== "string") attachSid = null;
                    wsConnect(function (ws) {
                        termCounter++;
                        var t = {
                            id: termCounter,
                            sid: null,
                            term: null,
                            fit: null,
                            div: null,
                            tab: null,
                            closeBtn: null,
                            lastCols: 0,
                            lastRows: 0
                        };
                        t.div = document.createElement("div");
                        t.div.className = "term-view";
                        termContainer.appendChild(t.div);

                        t.tab = document.createElement("div");
                        t.tab.className = "term-tab";
                        t.tab.textContent = "Terminale " + t.id + " ";
                        t.closeBtn = document.createElement("span");
                        t.closeBtn.className = "close";
                        t.closeBtn.textContent = "✕";
                        t.tab.appendChild(t.closeBtn);
                        t.tab.addEventListener("click", function () { activateTerm(t); });
                        t.closeBtn.addEventListener("click", function (e) {
                            e.stopPropagation();
                            closeTerm(t);
                        });
                        termTabs.appendChild(t.tab);

                        t.term = new Terminal({ cursorBlink: true, fontSize: 14, fontFamily: "Menlo, Monaco, Consolas, monospace" });
                        t.fit = new FitAddon.FitAddon();
                        t.term.loadAddon(t.fit);
                        t.term.open(t.div);
                        t.observer = new ResizeObserver(function () {
                            if (t.div.offsetHeight > 0) fitTerm(t);
                        });
                        t.observer.observe(t.div);
                        t.term.onData(function (data) {
                            if (globalWs && globalWs.readyState === WebSocket.OPEN && t.sid) {
                                globalWs.send(JSON.stringify({type: "input", sid: t.sid, data: data}));
                            }
                        });
                        registerOsc52(t.term);
                        terms.push(t);
                        if (attachSid) t.sid = attachSid; else pendingTerms.push(t);
                        activateTerm(t);
                        setTimeout(function () {
                            t.fit.fit();
                            t.lastCols = t.term.cols;
                            t.lastRows = t.term.rows;
                            ws.send(JSON.stringify({type: attachSid ? "attach" : "new", sid: attachSid, cols: t.term.cols, rows: t.term.rows, cwd: curPath}));
                        }, 50);
                        termBar.classList.add("active");
                        updateTermToggle();
                    });
                }

                function activateTerm(t) {
                    if (activeTerm && activeTerm !== t) {
                        activeTerm.div.classList.remove("active");
                        activeTerm.tab.classList.remove("active");
                    }
                    activeTerm = t;
                    t.div.classList.add("active");
                    t.tab.classList.add("active");
                    fitTerm(t);
                    termFocus();
                }

                function closeTerm(t) {
                    var idx = terms.indexOf(t);
                    if (idx !== -1) terms.splice(idx, 1);
                    var pidx = pendingTerms.indexOf(t);
                    if (pidx !== -1) pendingTerms.splice(pidx, 1);
                    if (t.sid && globalWs && globalWs.readyState === WebSocket.OPEN) {
                        globalWs.send(JSON.stringify({type: "close", sid: t.sid}));
                    }
                    if (t.term) { t.term.dispose(); t.term = null; }
                    if (t.observer) { t.observer.disconnect(); t.observer = null; }
                    if (t.tab) t.tab.remove();
                    if (t.div) t.div.remove();
                    if (activeTerm === t) {
                        activeTerm = terms.length ? terms[terms.length - 1] : null;
                        if (activeTerm) activateTerm(activeTerm);
                        else hideTermPanel();
                    }
                }

                function updateTermToggle() {
                    var wrap = document.getElementById("term-wrap");
                    termBtn.textContent = (wrap && wrap.classList.contains("active")) ? "✖ Chiudi terminale" : "🖥️ Apri terminale";
                }

                function copyTextToClipboard(text) {
                    if (navigator.clipboard && navigator.clipboard.writeText) {
                        navigator.clipboard.writeText(text).catch(function () { legacyCopy(text); });
                    } else {
                        legacyCopy(text);
                    }
                }

                function legacyCopy(text) {
                    var ta = document.createElement("textarea");
                    ta.value = text;
                    ta.style.position = "fixed";
                    ta.style.opacity = "0";
                    document.body.appendChild(ta);
                    ta.focus();
                    ta.select();
                    try { document.execCommand("copy"); } catch (e) {}
                    document.body.removeChild(ta);
                }

                function decodeOsc52B64(b64) {
                    b64 = b64.replace(/-/g, "+").replace(/_/g, "/");
                    while (b64.length % 4) b64 += "=";
                    try {
                        return decodeURIComponent(escape(atob(b64)));
                    } catch (e) {
                        try { return atob(b64); } catch (e2) { return ""; }
                    }
                }

                function registerOsc52(termInstance) {
                    if (termInstance.parser && termInstance.parser.registerOscHandler) {
                        termInstance.parser.registerOscHandler(52, function (data) {
                            var parts = String(data).split(";");
                            var b64 = parts[parts.length - 1];
                            if (b64 && b64 !== "?") {
                                copyTextToClipboard(decodeOsc52B64(b64));
                            }
                            return true;
                        });
                    }
                }

                function fitTerm(t) {
                    if (t && t.term && t.fit) {
                        t.fit.fit();
                        if (t.sid && (t.term.cols !== t.lastCols || t.term.rows !== t.lastRows)) {
                            t.lastCols = t.term.cols;
                            t.lastRows = t.term.rows;
                            if (globalWs && globalWs.readyState === WebSocket.OPEN) {
                                globalWs.send(JSON.stringify({type: "resize", sid: t.sid, cols: t.term.cols, rows: t.term.rows}));
                            }
                        }
                    }
                }

                function startResize(t) {
                    return function (e) {
                        e.preventDefault();
                        e.stopPropagation();
                        var startY = e.clientY;
                        var startH = t.div.offsetHeight;
                        function onMove(ev) {
                            var h = startH + (ev.clientY - startY);
                            if (h < 120) h = 120;
                            t.div.style.height = h + "px";
                            fitTerm(t);
                        }
                        function onUp() {
                            document.removeEventListener("mousemove", onMove);
                            document.removeEventListener("mouseup", onUp);
                            document.body.style.cursor = "";
                            document.body.style.userSelect = "";
                        }
                        document.addEventListener("mousemove", onMove);
                        document.addEventListener("mouseup", onUp);
                        document.body.style.cursor = "ns-resize";
                        document.body.style.userSelect = "none";
                    };
                }

                window.addEventListener("resize", function () {
                    if (activeTerm) fitTerm(activeTerm);
                    if (typeof refreshEditor === "function") refreshEditor();
                });

                function termFocus() {
                    if (activeTerm && activeTerm.term) activeTerm.term.focus();
                }
        </script>"""
        html_content = index_template.replace(
            "{ROWS}",
            rows if rows else "<tr><td colspan='5'>Nessun contenuto presente</td></tr>"
        ).replace("{BODYCLASS}", {"term": "full-term", "editor": "full-editor"}.get(view, "") if (view != "term" or FileServerHandler.enable_terminal) else "").replace("{FULLVIEW}", json.dumps(view)).replace("{FULLFILE}", json.dumps(open_file).replace("</", "<\\/")).replace("{CRUMBS}", crumb_html).replace("{CURPATH}", esc(cur_str))
        if FileServerHandler.enable_terminal:
            html_content = html_content.replace("{TERM_UI}", TERM_UI).replace("{TERM_SCRIPTS}", TERM_SCRIPTS).replace("{TERM_TOGGLE}", TERM_TOGGLE)
        else:
            html_content = html_content.replace("{TERM_UI}", "").replace("{TERM_SCRIPTS}", "").replace("{TERM_TOGGLE}", "")
        data = html_content.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


# --- Avvio del server ---
def run_server(port=8080, directory="storage", user="admin", password="admin", enable_terminal=True):
    FileServerHandler.storage_dir = directory
    FileServerHandler.USERNAME = user
    FileServerHandler.PASSWORD = password
    FileServerHandler.enable_terminal = enable_terminal
    Path(directory).mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("", port), FileServerHandler)
    print(f"✅ Server avviato su http://localhost:{port}")
    print(f"📂 Directory di upload: {Path(directory).absolute()}")
    print(f"🔑 Username: {user}, Password: {password}")
    print(f"🖥️ Terminale: {'attivo' if enable_terminal else 'disabilitato (--no-terminal)'}")
    print("\n💡 Esempio di utilizzo completo:")
    print(f"python3 {sys.argv[0]} 8080 storage admin admin")
    print("Parametri:")
    print("1️⃣ Porta (default 8080)")
    print("2️⃣ Directory di storage (default 'storage')")
    print("3️⃣ Username login (default 'admin')")
    print("4️⃣ Password login (default 'admin')\n")

    print("🌐 Browser URL: http://<IP-VM>:8080")
    print("Funzionalità:")
    print("• Carica file tramite form o trascinando (drag & drop)")
    print("• Carica cartelle intere (mantiene la struttura delle sottocartelle)")
    print("• Scarica file con /download?file=nomefile")
    print("• Scarica cartelle come ZIP con /download-dir?dir=nomecartella")
    print("• Cancella file/cartelle con /delete?file=nome")
    print("• Lista contenuti con /list\n")

    print("📌 Esempi di chiamate curl:")

    print("# Upload file")
    print(f'curl -u {user}:{password} -F "file=@/percorso/del/file.txt" http://<IP-VM>:{port}/upload')

    print("# Upload cartella (mantiene la struttura)")
    print(f'curl -u {user}:{password} -F "file=@/percorso/cartella/file.txt;filename=cartella/file.txt" http://<IP-VM>:{port}/upload')

    print("# Download file")
    print(f'curl -u {user}:{password} -O http://<IP-VM>:{port}/download?file=file.txt')

    print("# Download cartella come ZIP")
    print(f'curl -u {user}:{password} -OJ http://<IP-VM>:{port}/download-dir?dir=cartella')

    print("# Cancella file o cartella")
    print(f'curl -u {user}:{password} -X GET http://<IP-VM>:{port}/delete?file=file.txt')

    print("# Lista contenuti")
    print(f'curl -u {user}:{password} http://<IP-VM>:{port}/list\n')

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Arresto server...")
    finally:
        server.server_close()


# --- Main ---
if __name__ == "__main__":
    port = 8080
    directory = "storage"
    user = "admin"
    password = "admin"

    if len(sys.argv) >= 2:
        try:
            port = int(sys.argv[1])
        except ValueError:
            print("⚠️ Porta non valida, uso default 8080")
    if len(sys.argv) >= 3:
        directory = sys.argv[2]
    if len(sys.argv) >= 5:
        user = sys.argv[3]
        password = sys.argv[4]

    enable_terminal = "--no-terminal" not in sys.argv

    run_server(port, directory, user, password, enable_terminal)
