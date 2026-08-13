#!/usr/bin/env python3
import os
import sys
import io
import time
import shutil
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from pathlib import Path
import html
import base64

class FileServerHandler(BaseHTTPRequestHandler):
    storage_dir = "storage"
    USERNAME = "admin"
    PASSWORD = "admin"

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

    # --- Gestione GET ---
    def do_GET(self):
        if not self.authenticate():
            return

        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

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

        else:
            self.send_error(404, "Not found")

    # --- Gestione POST per upload (multipli file/cartelle) ---
    def do_POST(self):
        if not self.authenticate():
            return

        parsed = urlparse(self.path)
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
            </style>
        </head>
        <body>
            <h1>📁 File Server</h1>
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
        </body>
        </html>
        """
        html_content = index_template.replace(
            "{ROWS}",
            rows if rows else "<tr><td colspan='4'>Nessun contenuto presente</td></tr>"
        ).replace("{CRUMBS}", crumb_html).replace("{CURPATH}", html.escape(browse_path))
        data = html_content.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


# --- Avvio del server ---
def run_server(port=8080, directory="storage", user="admin", password="admin"):
    FileServerHandler.storage_dir = directory
    FileServerHandler.USERNAME = user
    FileServerHandler.PASSWORD = password
    Path(directory).mkdir(parents=True, exist_ok=True)
    server = HTTPServer(("", port), FileServerHandler)
    print(f"✅ Server avviato su http://localhost:{port}")
    print(f"📂 Directory di upload: {Path(directory).absolute()}")
    print(f"🔑 Username: {user}, Password: {password}")
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

    run_server(port, directory, user, password)
