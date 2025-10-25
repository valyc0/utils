#!/usr/bin/env python3
import os
import sys
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

    # --- Gestione GET ---
    def do_GET(self):
        if not self.authenticate():
            return

        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/":
            return self.send_index_page()

        elif parsed.path == "/list":
            files = [f.name for f in Path(self.storage_dir).glob("*") if f.is_file()]
            data = "\n".join(files).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        elif parsed.path == "/download":
            filename = params.get("file", [None])[0]
            if not filename:
                return self.send_error(400, "Parametro 'file' mancante. Usa ?file=nomefile")
            file_path = Path(self.storage_dir) / filename
            if not file_path.exists():
                return self.send_error(404, "File non trovato")
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(file_path.stat().st_size))
            self.end_headers()
            with open(file_path, "rb") as f:
                while chunk := f.read(8192):
                    self.wfile.write(chunk)

        elif parsed.path == "/delete":
            filename = params.get("file", [None])[0]
            if not filename:
                return self.send_error(400, "Parametro 'file' mancante per cancellazione")
            file_path = Path(self.storage_dir) / filename
            if not file_path.exists():
                return self.send_error(404, "File non trovato")
            try:
                file_path.unlink()
                self.send_response(303)
                self.send_header("Location", "/")
                self.end_headers()
            except Exception as e:
                self.send_error(500, f"Errore durante la cancellazione: {e}")

        else:
            self.send_error(404, "Not found")

    # --- Gestione POST per upload ---
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

        boundary = ctype.split("boundary=")[1]
        remainbytes = int(self.headers['Content-length'])
        line = self.rfile.readline()
        remainbytes -= len(line)
        if not boundary.encode() in line:
            return self.send_error(400, "Malformed form data")

        line = self.rfile.readline()
        remainbytes -= len(line)
        filename = line.decode().split('filename="')[1].split('"')[0]
        filename = os.path.basename(filename)

        # Salta header
        while True:
            line = self.rfile.readline()
            remainbytes -= len(line)
            if line.strip() == b'':
                break

        outpath = Path(self.storage_dir) / filename
        with open(outpath, 'wb') as out:
            preline = self.rfile.readline()
            remainbytes -= len(preline)
            while remainbytes > 0:
                line = self.rfile.readline()
                remainbytes -= len(line)
                if boundary.encode() in line:
                    preline = preline.rstrip(b'\r\n')
                    out.write(preline)
                    break
                else:
                    out.write(preline)
                    preline = line

        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    # --- Pagina HTML principale ---
    def send_index_page(self):
        files = [f.name for f in Path(self.storage_dir).glob("*") if f.is_file()]
        rows = "".join(
            f"<li>"
            f"<a href='/download?file={html.escape(f)}'>{html.escape(f)}</a> "
            f"<a href='/delete?file={html.escape(f)}' onclick='return confirm(\"Confermi cancellazione {html.escape(f)}?\");'>[Elimina]</a>"
            f"</li>"
            for f in files
        )

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>File Server</title>
            <style>
                body {{ font-family: sans-serif; margin: 40px; }}
                input[type=file] {{ margin: 10px 0; }}
                ul {{ list-style-type: none; padding: 0; }}
                li {{ margin: 4px 0; }}
            </style>
        </head>
        <body>
            <h1>📁 File Server</h1>
            <form method="POST" enctype="multipart/form-data" action="/upload">
                <input type="file" name="file" required>
                <input type="submit" value="Carica">
            </form>
            <h2>File disponibili</h2>
            <ul>{rows if rows else "<li>Nessun file presente</li>"}</ul>
        </body>
        </html>
        """
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
    print("• Carica file tramite form")
    print("• Scarica file con /download?file=nomefile")
    print("• Cancella file con /delete?file=nomefile")
    print("• Lista file con /list\n")

    print("📌 Esempi di chiamate curl:")

    print("# Upload file")
    print(f'curl -u {user}:{password} -F "file=@/percorso/del/file.txt" http://<IP-VM>:{port}/upload')

    print("# Download file")
    print(f'curl -u {user}:{password} -O http://<IP-VM>:{port}/download?file=file.txt')

    print("# Cancella file")
    print(f'curl -u {user}:{password} -X GET http://<IP-VM>:{port}/delete?file=file.txt')

    print("# Lista file")
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

