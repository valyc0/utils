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
from urllib.parse import urlparse, parse_qs
from pathlib import Path
import html
import base64
import signal

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
        """Risolve `name` dentro storage_dir impedendo path traversal."""
        base = Path(self.storage_dir).resolve()
        target = (base / name).resolve()
        if base != target and base not in target.parents:
            return None
        return target

    @staticmethod
    def sanitize_relative_path(filename):
        """Normalizza un percorso relativo di upload mantenendo le sottocartelle."""
        filename = filename.replace("\\", "/")
        parts = [p for p in filename.split("/") if p not in ("", ".", "..")]
        return "/".join(parts) if parts else None

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

    # --- Gestione terminale interattivo (PTY) ---
    def term_new(self):
        pid, master_fd = pty.fork()
        if pid == 0:
            os.chdir(self.storage_dir)
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            env["PS1"] = "\\[\\e[32m\\]\\w\\[\\e[0m\\] $ "
            os.execvpe("/bin/bash", ["/bin/bash", "--norc", "--noprofile"], env)
        sid = uuid.uuid4().hex
        self.sessions[sid] = {"pid": pid, "fd": master_fd, "dir": self.storage_dir}
        body = json.dumps({"sid": sid}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def term_read(self, sid):
        sess = self.sessions.get(sid)
        if not sess:
            return self.send_error(404, "Sessione non trovata")
        fd = sess["fd"]
        data = b""
        try:
            while True:
                r, _, _ = select.select([fd], [], [], 0)
                if not r:
                    break
                chunk = os.read(fd, 8192)
                if not chunk:
                    break
                data += chunk
        except (OSError, ValueError):
            pass
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def term_write(self, sid):
        sess = self.sessions.get(sid)
        if not sess:
            return self.send_error(404, "Sessione non trovata")
        length = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(length) if length else b""
        if data:
            try:
                os.write(sess["fd"], data)
            except OSError:
                pass
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def term_resize(self, sid):
        sess = self.sessions.get(sid)
        if not sess:
            return self.send_error(404, "Sessione non trovata")
        try:
            cols = int(self.params.get("cols", ["80"])[0])
            rows = int(self.params.get("rows", ["24"])[0])
            fcntl.ioctl(sess["fd"], termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except (ValueError, OSError):
            pass
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def term_close(self, sid):
        sess = self.sessions.pop(sid, None)
        if sess:
            try:
                os.kill(sess["pid"], signal.SIGHUP)
            except OSError:
                pass
            try:
                os.close(sess["fd"])
            except OSError:
                pass
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # --- Gestione GET ---
    def do_GET(self):
        if not self.authenticate():
            return

        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        self.params = params

        if parsed.path == "/":
            browse_path = params.get("path", [""])[0]
            return self.send_index_page(browse_path)

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
            if file_path is None or not file_path.is_file():
                return self.send_error(404, "File non trovato")
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{Path(filename).name}"')
            self.send_header("Content-Length", str(file_path.stat().st_size))
            self.end_headers()
            with open(file_path, "rb") as f:
                while chunk := f.read(8192):
                    self.wfile.write(chunk)

        elif parsed.path == "/download-dir":
            dirname = params.get("dir", [None])[0]
            if not dirname:
                return self.send_error(400, "Parametro 'dir' mancante. Usa ?dir=nomecartella")
            dir_path = self.resolve_in_storage(dirname)
            if dir_path is None or not dir_path.is_dir():
                return self.send_error(404, "Directory non trovata")

            # Crea lo ZIP in memoria mantenendo i percorsi relativi
            base = Path(self.storage_dir).resolve()
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, _, fnames in os.walk(dir_path):
                    for fname in fnames:
                        full = Path(root) / fname
                        rel = full.relative_to(base)
                        zf.write(full, rel.as_posix())
            data = buf.getvalue()

            zip_name = dirname.replace("\\", "/").rstrip("/").split("/")[-1]
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{zip_name}.zip"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        elif parsed.path == "/delete":
            filename = params.get("file", [None])[0]
            if not filename:
                return self.send_error(400, "Parametro 'file' mancante per cancellazione")
            target = self.resolve_in_storage(filename)
            if target is None or not target.exists():
                return self.send_error(404, "File o cartella non trovato")
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

        elif parsed.path == "/term/new":
            if not self.enable_terminal:
                return self.send_error(404, "Terminale disabilitato")
            return self.term_new()

        elif parsed.path == "/term/read":
            if not self.enable_terminal:
                return self.send_error(404, "Terminale disabilitato")
            return self.term_read(params.get("sid", [None])[0])

        elif parsed.path == "/term/resize":
            if not self.enable_terminal:
                return self.send_error(404, "Terminale disabilitato")
            return self.term_resize(params.get("sid", [None])[0])

        elif parsed.path == "/term/close":
            if not self.enable_terminal:
                return self.send_error(404, "Terminale disabilitato")
            return self.term_close(params.get("sid", [None])[0])

        else:
            self.send_error(404, "Not found")

    # --- Gestione POST per upload (multipli file/cartelle) ---
    def do_POST(self):
        if not self.authenticate():
            return

        parsed = urlparse(self.path)
        if parsed.path != "/upload":
            if parsed.path == "/term/write":
                if not self.enable_terminal:
                    return self.send_error(404, "Terminale disabilitato")
                params = parse_qs(parsed.query)
                return self.term_write(params.get("sid", [None])[0])
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
        upload_path = ""
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
                upload_path = self.sanitize_relative_path(value.decode(errors="replace")) or ""
                continue

            rel = self.sanitize_relative_path(filename) if filename else None
            if rel:
                rel = f"{upload_path}/{rel}" if upload_path else rel
                outpath = Path(self.storage_dir) / rel
                outpath.parent.mkdir(parents=True, exist_ok=True)
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

        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    # --- Pagina HTML principale ---
    def send_index_page(self, browse_path=""):
        base = Path(self.storage_dir).resolve()
        current = self.resolve_in_storage(browse_path) if browse_path else base
        if current is None or not current.is_dir():
            return self.send_error(404, "Cartella non trovata")

        entries = sorted(
            current.iterdir(),
            key=lambda p: (p.is_file(), p.name.lower())
        )

        # Breadcrumb per la navigazione
        crumbs = [('<a href="/">🏠 Root</a>', "")]
        acc = ""
        for part in browse_path.split("/"):
            if not part:
                continue
            acc = f"{acc}/{part}" if acc else part
            crumbs.append((f"<a href='/?path={html.escape(acc)}'>{html.escape(part)}</a>", acc))
        crumb_html = " / ".join(c for c, _ in crumbs)
        if browse_path:
            crumb_html += f" <a href='/download-dir?dir={html.escape(browse_path)}'>⬇️ Scarica cartella ZIP</a>"

        rows = ""
        for p in entries:
            name = p.name
            rel = f"{browse_path}/{name}" if browse_path else name
            st = p.stat()
            size = self.format_size(st.st_size) if p.is_file() else "—"
            date = self.format_date(st.st_mtime)
            if p.is_dir():
                rows += (
                    f"<tr><td>📁 <a href='/?path={html.escape(rel)}'>{html.escape(name)}/</a></td>"
                    f"<td>{size}</td><td>{date}</td>"
                    f"<td><a href='/?path={html.escape(rel)}'>Apri</a> "
                    f"<a href='/download-dir?dir={html.escape(rel)}'>Scarica ZIP</a> "
                    f"<a href='/delete?file={html.escape(rel)}' onclick='return confirm(\"Confermi cancellazione cartella {html.escape(rel)}?\");'>[Elimina]</a></td></tr>"
                )
            else:
                rows += (
                    f"<tr><td>📄 <a href='/download?file={html.escape(rel)}'>{html.escape(name)}</a></td>"
                    f"<td>{size}</td><td>{date}</td>"
                    f"<td><a href='/download?file={html.escape(rel)}'>Scarica</a> "
                    f"<a href='/delete?file={html.escape(rel)}' onclick='return confirm(\"Confermi cancellazione {html.escape(rel)}?\");'>[Elimina]</a></td></tr>"
                )

        index_template = """<!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>File Server</title>
            <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.css">
            <style>
                body { font-family: sans-serif; margin: 40px; }
                input[type=file] { margin: 10px 0; }
                table { border-collapse: collapse; margin-top: 10px; }
                th, td { text-align: left; padding: 4px 14px 4px 0; border-bottom: 1px solid #eee; }
                th { font-size: 0.9em; color: #666; }
                #drop-zone { border: 2px dashed #aaa; border-radius: 8px; padding: 30px; text-align: center; color: #666; margin: 20px 0; cursor: pointer; }
                #drop-zone.dragover { border-color: #4caf50; background: #e8f5e9; color: #2e7d32; }
                #status { display: none; margin: 10px 0; padding: 10px; background: #fff3cd; border-radius: 4px; }
                .progress { display: none; width: 100%; background: #eee; border-radius: 4px; height: 16px; margin: 10px 0; }
                .progress > div { height: 100%; width: 0%; background: #4caf50; border-radius: 4px; }
                #crumbs { margin: 8px 0 12px; padding: 8px 12px; background: #f5f5f5; border-radius: 6px; font-size: 0.95em; }
                #crumbs a { color: #1976d2; text-decoration: none; }
                #crumbs a:hover { text-decoration: underline; }
                #term-toggle { margin: 0 0 10px; padding: 8px 14px; border: none; border-radius: 6px; background: #263238; color: #fff; font-size: 0.95em; cursor: pointer; }
                #term-toggle:hover { background: #37474f; }
                #term-bar { display: none; align-items: center; margin: 10px 0 0; }
                #term-bar.active { display: flex; }
                #term-tabs { display: flex; gap: 4px; flex-wrap: wrap; }
                .term-tab { padding: 6px 12px; border: 1px solid #455a64; border-radius: 6px 6px 0 0; background: #eceff1; cursor: pointer; font-size: 0.9em; user-select: none; }
                .term-tab.active { background: #263238; color: #fff; }
                .term-tab .close { margin-left: 8px; color: #999; cursor: pointer; font-weight: bold; }
                .term-tab .close:hover { color: #d32f2f; }
                #term-add { margin-left: 8px; padding: 6px 12px; border: none; border-radius: 6px; background: #4caf50; color: #fff; cursor: pointer; font-size: 0.95em; }
                #term-add:hover { background: #388e3c; }
                #term-container { margin-bottom: 10px; }
                .term-view { display: none; position: relative; height: 360px; min-height: 120px; padding: 8px; background: #000; border-radius: 0 6px 6px 6px; border: 1px solid #455a64; }
                .term-view.active { display: block; }
                .term-resize-handle { position: absolute; left: 0; right: 0; bottom: 0; height: 8px; cursor: ns-resize; z-index: 10; background: rgba(255,255,255,0.12); border-top: 1px solid #455a64; border-radius: 0 0 6px 6px; }
                .term-resize-handle:hover { background: #37474f; }
            </style>
        </head>
        <body>
            <h1>📁 File Server</h1>
            {TERM_UI}
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

            <h2>Contenuti disponibili</h2>
            <table>
                <thead><tr><th>Nome</th><th>Dimensione</th><th>Data modifica</th><th>Azioni</th></tr></thead>
                <tbody>{ROWS}</tbody>
            </table>

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
                            statusEl.textContent = "✅ Upload completato!";
                            setTimeout(function () { location.reload(); }, 500);
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
            {TERM_SCRIPTS}
        </body>
        </html>
        """
        TERM_UI = """<div><button id="term-toggle">🖥️ Apri terminale</button></div>
        <div id="term-bar">
            <div id="term-tabs"></div>
            <button id="term-add" title="Nuovo terminale">＋</button>
        </div>
        <div id="term-container"></div>"""

        TERM_SCRIPTS = """<script src="https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.11.0/lib/addon-fit.js"></script>
        <script>
                var termBtn = document.getElementById("term-toggle");
                var termBar = document.getElementById("term-bar");
                var termTabs = document.getElementById("term-tabs");
                var termContainer = document.getElementById("term-container");
                var termAddBtn = document.getElementById("term-add");
                var terms = [];
                var activeTerm = null;
                var termCounter = 0;

                termBtn.addEventListener("click", function () {
                    if (terms.length) {
                        closeAllTerms();
                    } else {
                        openTerm();
                    }
                });

                termAddBtn.addEventListener("click", openTerm);

                function openTerm() {
                    var xhr = new XMLHttpRequest();
                    xhr.open("GET", "/term/new", true);
                    xhr.onload = function () {
                        if (xhr.status === 200) {
                            var resp = JSON.parse(xhr.responseText);
                            termCounter++;
                            var t = {
                                id: termCounter,
                                sid: resp.sid,
                                term: null,
                                fit: null,
                                poll: null,
                                div: null,
                                tab: null,
                                closeBtn: null
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
                            t.handle = document.createElement("div");
                            t.handle.className = "term-resize-handle";
                            t.handle.title = "Trascina per ridimensionare l'altezza";
                            t.handle.addEventListener("mousedown", startResize(t));
                            t.div.appendChild(t.handle);
                            t.term.onData(function (data) {
                                var w = new XMLHttpRequest();
                                w.open("POST", "/term/write?sid=" + encodeURIComponent(t.sid), true);
                                w.setRequestHeader("Content-Type", "application/octet-stream");
                                w.send(data);
                            });
                            t.poll = setInterval(function () { pollTerm(t); }, 100);
                            registerOsc52(t.term);
                            terms.push(t);
                            activateTerm(t);
                            termBar.classList.add("active");
                            updateTermToggle();
                            termFocus();
                        }
                    };
                    xhr.send();
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
                    if (t.poll) { clearInterval(t.poll); t.poll = null; }
                    if (t.sid) {
                        var xhr = new XMLHttpRequest();
                        xhr.open("GET", "/term/close?sid=" + encodeURIComponent(t.sid), true);
                        xhr.send();
                    }
                    if (t.term) { t.term.dispose(); t.term = null; }
                    if (t.observer) { t.observer.disconnect(); t.observer = null; }
                    if (t.tab) t.tab.remove();
                    if (t.div) t.div.remove();
                    if (activeTerm === t) {
                        activeTerm = terms.length ? terms[terms.length - 1] : null;
                        if (activeTerm) activateTerm(activeTerm);
                        else updateTermToggle();
                    }
                }

                function closeAllTerms() {
                    while (terms.length) closeTerm(terms[terms.length - 1]);
                    termBar.classList.remove("active");
                    updateTermToggle();
                }

                function updateTermToggle() {
                    if (terms.length) {
                        termBtn.textContent = "✖ Chiudi terminali";
                    } else {
                        termBtn.textContent = "🖥️ Apri terminale";
                    }
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

                function pollTerm(t) {
                    if (!t.sid) return;
                    var xhr = new XMLHttpRequest();
                    xhr.open("GET", "/term/read?sid=" + encodeURIComponent(t.sid), true);
                    xhr.onload = function () {
                        if (xhr.status === 200 && xhr.response) {
                            t.term.write(xhr.response);
                        }
                    };
                    xhr.send();
                }

                function fitTerm(t) {
                    if (t && t.term && t.fit) {
                        t.fit.fit();
                        if (t.sid && (t.term.cols !== t.lastCols || t.term.rows !== t.lastRows)) {
                            t.lastCols = t.term.cols;
                            t.lastRows = t.term.rows;
                            var xhr = new XMLHttpRequest();
                            xhr.open("GET", "/term/resize?sid=" + encodeURIComponent(t.sid) + "&cols=" + t.term.cols + "&rows=" + t.term.rows, true);
                            xhr.send();
                        }
                    }
                }

                function startResize(t) {
                    return function (e) {
                        e.preventDefault();
                        e.stopPropagation();
                        var startY = e.clientY;
                        var startH = t.div.offsetHeight;
                        var moving = false;
                        function onMove(ev) {
                            moving = true;
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
                });

                function termFocus() {
                    if (activeTerm && activeTerm.term) activeTerm.term.focus();
                }
        </script>"""
        html_content = index_template.replace(
            "{ROWS}",
            rows if rows else "<tr><td colspan='4'>Nessun contenuto presente</td></tr>"
        ).replace("{CRUMBS}", crumb_html).replace("{CURPATH}", html.escape(browse_path))
        if FileServerHandler.enable_terminal:
            html_content = html_content.replace("{TERM_UI}", TERM_UI).replace("{TERM_SCRIPTS}", TERM_SCRIPTS)
        else:
            html_content = html_content.replace("{TERM_UI}", "").replace("{TERM_SCRIPTS}", "")
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
