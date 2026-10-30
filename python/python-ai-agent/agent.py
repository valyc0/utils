#!/usr/bin/env python3
"""Coding agent da riga di comando (port Python di spring-boot-ai-agent).

Uso: python agent.py [directory-di-lavoro]
"""
import fnmatch
import http.client
import json
import os
import re
import select
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

try:
    import termios
    import tty
except ImportError:  # Windows
    termios = tty = None

# Massimo numero di chiamate ai tool per ogni richiesta, e messaggi tenuti in cronologia.
MAX_ITERAZIONI, MAX_MESSAGGI = 200, 1000
WINDOWS = os.name == "nt"
OS_NAME = {"nt": "Windows", "posix": sys.platform}.get(os.name, os.name)
if sys.platform.startswith("linux"):
    OS_NAME = "Linux"
elif sys.platform == "darwin":
    OS_NAME = "Mac OS X"


# ---------------------------------------------------------------- configurazione

def load_env(path=".env"):
    """Legge .env (KEY=VALUE, # commenti). Le variabili d'ambiente hanno la precedenza."""
    props = {}
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line[0] in "#!" or "=" not in line:
                continue
            k, v = line.split("=", 1)
            props[k.strip()] = v.strip()
    except OSError:
        pass
    props.update(os.environ)
    return props


def flag(env, name):
    return str(env.get(name, "false")).strip().lower() == "true"


# ---------------------------------------------------------------- console

class Cancelled(Exception):
    pass


class Console:
    """Input da terminale. Su Unix mette la tastiera in modalità raw solo mentre il modello lavora, così ESC
    arriva subito; per leggere una riga (prompt o conferma) ripristina la modalità normale.
    Se stdin non è un terminale, ESC non è disponibile."""

    def __init__(self):
        self.lock = threading.Condition()
        self.saved = None
        if termios and sys.stdin.isatty():
            try:
                self.saved = termios.tcgetattr(sys.stdin.fileno())
            except termios.error:
                pass
        self.watcher = None
        self.running = self.paused = self.reading = False

    def can_interrupt(self):
        return self.saved is not None

    def set_raw(self):
        if self.saved:
            tty.setcbreak(sys.stdin.fileno())  # niente canonical mode né echo, ma Ctrl+C resta attivo

    def restore(self):
        if self.saved:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self.saved)

    def read_line(self, prompt):
        """Legge una riga (prompt principale o conferma). Sospende il watcher di ESC mentre legge."""
        with self.lock:
            watching, prev_paused = self.running, self.paused
            self.paused = True
            while self.reading:
                self.lock.wait(0.05)
        if watching:
            self.restore()
        try:
            sys.stdout.write(prompt)
            sys.stdout.flush()
            line = sys.stdin.readline()
            if line == "":
                return None
            return line.rstrip("\r\n")
        except (OSError, UnicodeDecodeError):
            return None
        finally:
            if watching:
                self.set_raw()
            with self.lock:
                self.paused = prev_paused
                self.lock.notify_all()

    def watch(self, cancel, on_cancel):
        """Avvia il watcher della tastiera: alla pressione di ESC chiama on_cancel."""
        if not self.saved:
            return
        with self.lock:
            self.paused, self.running = False, True
        self.set_raw()

        def loop():
            try:
                fd = sys.stdin.fileno()
                while self.running:
                    with self.lock:
                        while self.paused and self.running:
                            self.lock.wait(0.05)
                    if not self.running:
                        break
                    if not select.select([fd], [], [], 0.02)[0]:
                        continue
                    with self.lock:
                        if self.paused or not select.select([fd], [], [], 0)[0]:
                            continue
                        self.reading = True
                        try:
                            c = os.read(fd, 1)
                        finally:
                            self.reading = False
                            self.lock.notify_all()
                    if c == b"\x1b":
                        cancel.set()
                        on_cancel()
                        break
            except Exception:
                pass  # terminale chiuso o interrotto: il chiamante ripristina comunque lo stato

        self.watcher = threading.Thread(target=loop, name="esc-watcher", daemon=True)
        self.watcher.start()

    def stop_watching(self):
        with self.lock:
            self.running = False
            t, self.watcher = self.watcher, None
            self.lock.notify_all()
        if t:
            t.join(0.5)
        self.restore()
        with self.lock:
            self.paused = False


class ReasoningPrinter:
    """Mostra in streaming il reasoning del modello (delta.reasoning_content), in corsivo se abilitato."""

    ANSI = sys.stdout.isatty()

    def __init__(self, enabled):
        self.enabled, self.open = enabled, False

    def reasoning(self, s):
        if not self.enabled:
            return
        if not self.open:
            sys.stdout.write(("\x1b[3m" if self.ANSI else "") + "[reasoning] ")
            self.open = True
        sys.stdout.write(s)
        sys.stdout.flush()

    def content(self):
        if self.enabled and self.open:
            sys.stdout.write(("\x1b[0m" if self.ANSI else "") + "\n[risposta] ")
            self.open = False

    def end(self):
        """Chiude il blocco di reasoning, se aperto. Ritorna True se ha stampato qualcosa."""
        if not self.enabled or not self.open:
            return False
        print("\x1b[0m" if self.ANSI else "")
        self.open = False
        return True


# ---------------------------------------------------------------- tool

def _schema(name, description, **params):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": {k: {"type": "string", "description": d} for k, d in params.items()},
                       "required": list(params)}}}


TOOL_SCHEMAS = [
    _schema("listFiles", "Elenca ricorsivamente i file di una directory (relativa)",
            dir="directory relativa, '.' per la root"),
    _schema("readFile", "Legge il contenuto di un file", path="percorso relativo"),
    _schema("writeFile", "Crea o sovrascrive un file con il contenuto completo (crea le directory mancanti)",
            path="percorso relativo", content="contenuto completo"),
    _schema("editFile", "Modifica un file sostituendo un testo esatto (old deve comparire una volta sola)",
            path="percorso relativo", old="testo esatto da sostituire", replacement="testo sostitutivo"),
    _schema("runCommand", "Esegue un comando shell nella root (es. mvn -q compile) e restituisce exit code e output",
            command="comando shell"),
    _schema("searchInFiles", "Cerca un testo (regex, case-insensitive) nei file di testo sotto una directory; "
            "restituisce 'file:riga: testo' con 2 righe di contesto ('file-riga- testo')",
            pattern="espressione regolare", dir="directory relativa, '.' per la root",
            glob="glob sul nome file, es. '*.java'; vuoto per tutti"),
    _schema("gitDiff", "Mostra le modifiche non ancora committate (git diff HEAD), opzionalmente per un solo file",
            path="file relativo, vuoto per tutto"),
]
WEB_SCHEMAS = [
    _schema("webSearch", "Cerca sul web (DuckDuckGo) e restituisce titolo, URL e descrizione dei primi risultati",
            query="testo da cercare"),
    _schema("readWebPage", "Scarica una pagina web (http/https) e ne restituisce il testo, senza HTML",
            url="URL completo della pagina"),
]


class Tools:
    """cancel è condiviso con il watcher della tastiera, così l'interruzione ferma anche i comandi in corso."""

    def __init__(self, root, console, confirm, cancel, before_tool, web):
        self.root, self.console, self.confirm, self.cancel = root, console, confirm, cancel
        self.before_tool, self.calls = before_tool, 0
        self.schemas = TOOL_SCHEMAS + (WEB_SCHEMAS if web else [])
        self.names = {s["function"]["name"] for s in self.schemas}

    def dispatch(self, name, args):
        if name not in self.names:
            return f"ERRORE: tool sconosciuto {name}"
        fn = getattr(self, name)
        try:
            return fn(**args)
        except TypeError as e:
            return f"ERRORE: argomenti non validi: {e}"

    def _run(self, name, detail, action):
        """Stampa la chiamata, controlla interruzione e limite di chiamate, poi esegue l'azione convertendo le eccezioni."""
        self.before_tool()
        print(f"  [{name}] {detail}", flush=True)
        if self.cancel.is_set():
            return "interrotto dall'utente"
        self.calls += 1
        if self.calls > MAX_ITERAZIONI:
            return f"ERRORE: limite di {MAX_ITERAZIONI} chiamate ai tool raggiunto, fermati e rispondi"
        try:
            return action()
        except Exception as e:
            return f"ERRORE: {type(e).__name__}: {e}"

    def _p(self, f):
        r = Path(os.path.normpath(self.root / (f or ".")))
        if r != self.root and self.root not in r.parents:
            raise ValueError("fuori dalla directory")
        return r

    def _rel(self, f):
        return f.relative_to(self.root).as_posix()

    def listFiles(self, dir):
        def action():
            base = self._p(dir)
            if not base.is_dir():
                return "la directory non esiste"
            out = []
            for dp, dns, fns in os.walk(base):
                dns.sort()
                for fn in sorted(fns):
                    f = Path(dp) / fn
                    if not f.is_file():
                        continue
                    r = self._rel(f)
                    if r.startswith(".git") or r.startswith("target"):
                        continue
                    if self.cancel.is_set() or len(out) >= 2000:
                        break
                    out.append(r)
                else:
                    continue
                break
            return "\n".join(out) + "\n(fine elenco; se vuoto non ci sono file: crea pure i file)"
        return self._run("listFiles", dir, action)

    def readFile(self, path):
        return self._run("readFile", path, lambda: self._p(path).read_bytes().decode("utf-8"))

    def writeFile(self, path, content):
        def action():
            p = self._p(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8", newline="")
            return "ok"
        return self._run("writeFile", path, action)

    def editFile(self, path, old, replacement):
        def action():
            p = self._p(path)
            c = p.read_bytes().decode("utf-8")
            if not old or c.count(old) != 1:
                return "errore: 'old' assente o non unico"
            p.write_text(c.replace(old, replacement), encoding="utf-8", newline="")
            return "ok"
        return self._run("editFile", path, action)

    def runCommand(self, command):
        def action():
            if self.confirm:
                answer = self.console.read_line("  Eseguire? [s/N] ")
                if answer is None or not answer.strip().lower().startswith("s"):
                    return "comando rifiutato dall'utente"
            if self.cancel.is_set():
                return "interrotto dall'utente"
            argv = ["cmd", "/c", command] if WINDOWS else ["bash", "-c", command]
            pr = subprocess.Popen(argv, cwd=self.root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, start_new_session=not WINDOWS)
            chunks = []

            def reader():
                for b in iter(lambda: pr.stdout.read(8192), b""):
                    chunks.append(b)
            t = threading.Thread(target=reader, daemon=True)
            t.start()
            deadline, status = time.monotonic() + 300, None
            while status is None and pr.poll() is None:
                time.sleep(0.1)
                status = "interrotto" if self.cancel.is_set() else "timeout" if time.monotonic() > deadline else None
            if status:
                try:
                    os.killpg(pr.pid, 9) if not WINDOWS else pr.kill()
                except OSError:
                    pr.kill()
            try:
                pr.wait(2)
            except subprocess.TimeoutExpired:
                pass
            t.join(1)
            text = b"".join(chunks).decode("utf-8", errors="replace")
            return f"exit={status if status else pr.returncode}\n" + (text[-50000:] if len(text) > 50000 else text)
        return self._run("runCommand", command, action)

    def searchInFiles(self, pattern, dir, glob=""):
        def action():
            re_ = re.compile(pattern, re.IGNORECASE)
            g = glob if glob and glob.strip() else "*"
            skip = re.compile(r"(.*/)?(\.git|\.idea|target|build|node_modules|generated)/.*")
            out, n = [], 0
            base = self._p(dir)
            for dp, dns, fns in os.walk(base):
                dns.sort()
                for fn in sorted(fns):
                    if self.cancel.is_set():
                        break
                    f = Path(dp) / fn
                    if not f.is_file():
                        continue
                    name = self._rel(f)
                    if skip.fullmatch(name) or not fnmatch.fnmatchcase(fn, g):
                        continue
                    try:
                        lines = f.read_text(encoding="utf-8").splitlines()
                    except (OSError, UnicodeDecodeError):
                        continue  # binario
                    last = -1
                    for i in range(len(lines)):
                        if n >= 500:
                            break
                        if not re_.search(lines[i]):
                            continue
                        n += 1
                        if i - 2 > last + 1 and last >= 0:
                            out.append("--")
                        end = min(len(lines) - 1, i + 2)
                        for j in range(max(last + 1, i - 2), end + 1):
                            out.append(f"{name}{':' if j == i else '-'}{j + 1}{': ' if j == i else '- '}{lines[j].strip()}")
                        last = end
                else:
                    continue
                break
            return "nessun risultato" if n == 0 else "\n".join(out) + ("\n(troncato a 500 risultati)" if n >= 500 else "")
        return self._run("searchInFiles", f"{pattern} in {dir} {glob}", action)

    def gitDiff(self, path=""):
        def action():
            cmd = ["git", "diff", "HEAD"]
            if path and path.strip():
                cmd += ["--", str(self._p(path).relative_to(self.root))]
            pr = subprocess.run(cmd, cwd=self.root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            out = pr.stdout.decode("utf-8", errors="replace")
            return "nessuna modifica" if not out.strip() else out[:50000] + "\n(troncato)" if len(out) > 50000 else out
        return self._run("gitDiff", path, action)

    # -- ricerca web su DuckDuckGo (HTML, senza API key) e lettura pagine, solo con AGENT_WEBSEARCH=true

    RESULT = re.compile(r'class="result__a" href="[^"]*?uddg=([^&"]+)[^"]*">(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL)

    @staticmethod
    def _get(url):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode(r.headers.get_content_charset() or "utf-8", errors="replace")

    @staticmethod
    def _text(h):
        t = re.sub(r"<[^>]+>", "", h)
        t = re.sub(r"&#(x?)([0-9a-fA-F]+);", lambda m: chr(int(m.group(2), 16 if m.group(1) else 10)), t)
        for a, b in (("&quot;", '"'), ("&lt;", "<"), ("&gt;", ">"), ("&nbsp;", " "), ("&amp;", "&")):
            t = t.replace(a, b)
        return t.strip()

    def webSearch(self, query):
        def action():
            page = self._get("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query))
            res = [f"{self._text(m.group(2))}\n{urllib.parse.unquote_plus(m.group(1))}\n{self._text(m.group(3))}"
                   for m in list(self.RESULT.finditer(page))[:10]]
            return "\n\n".join(res) or "nessun risultato"
        return self._run("webSearch", query, action)

    def readWebPage(self, url):
        def action():
            h = self._get(url)
            h = re.sub(r"(?is)<(script|style|head|nav|footer|svg)\b.*?</\1>", "", h)
            h = re.sub(r"(?i)<(br|/p|/div|/li|/h\d|/tr)[^>]*>", "\n", h)
            t = re.sub(r"\s*\n\s*", "\n", re.sub(r"[ \t]+", " ", self._text(h)))
            return t[:30000] + "\n(troncato)" if len(t) > 30000 else t
        return self._run("readWebPage", url, action)


# ---------------------------------------------------------------- client LLM

class LLM:
    """Client minimale (solo libreria standard) per /v1/chat/completions in streaming."""

    def __init__(self, url, key, model):
        u = urllib.parse.urlparse(url.rstrip("/"))
        self.https, self.host, self.port = u.scheme == "https", u.hostname, u.port
        self.path = u.path + "/v1/chat/completions"
        self.headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        self.model = model
        self.conn = None

    def abort(self):
        """Chiamato dal watcher di ESC: chiude il socket e sblocca la lettura in corso."""
        c = self.conn
        if c is not None and c.sock is not None:
            try:
                c.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def stream(self, messages, tools):
        """Genera i delta (dict) della risposta. Solleva eccezioni in caso di errore."""
        body = {"model": self.model, "messages": messages, "temperature": 0, "stream": True}
        if tools:
            body["tools"] = tools
        cls = http.client.HTTPSConnection if self.https else http.client.HTTPConnection
        self.conn = cls(self.host, self.port, timeout=600)
        try:
            self.conn.request("POST", self.path, json.dumps(body).encode(), self.headers)
            r = self.conn.getresponse()
            if r.status >= 400:
                raise RuntimeError(f"{r.status} {r.reason}: {r.read().decode('utf-8', errors='replace')}")
            while True:
                line = r.readline().decode("utf-8", errors="replace").strip()
                if not line and r.isclosed():
                    return
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                if chunk.get("error"):
                    raise RuntimeError(json.dumps(chunk["error"]))
                for ch in chunk.get("choices") or []:
                    if ch.get("delta"):
                        yield ch["delta"]
        finally:
            self.conn.close()


def trim(history):
    """Finestra scorrevole: ultimi MAX_MESSAGGI messaggi, partendo sempre da un messaggio utente."""
    if len(history) > MAX_MESSAGGI:
        del history[:len(history) - MAX_MESSAGGI]
    while history and history[0]["role"] != "user":
        history.pop(0)


def run_turn(llm, tools, history, reasoning, cancel):
    """Un turno: ciclo modello -> tool finché il modello risponde senza chiamare altri tool. Ritorna l'ultimo testo."""
    tail = ""
    while True:
        text, calls = "", {}
        for d in llm.stream(history, tools.schemas):
            if cancel.is_set():
                raise Cancelled()
            r = d.get("reasoning_content") or d.get("reasoning")
            if isinstance(r, str) and r:
                reasoning.reasoning(r)
            if d.get("content"):
                reasoning.content()
                sys.stdout.write(d["content"])
                sys.stdout.flush()
                text += d["content"]
                tail = d["content"]
            for tc in d.get("tool_calls") or []:
                c = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "args": ""})
                c["id"] = tc.get("id") or c["id"]
                fn = tc.get("function") or {}
                c["name"] += fn.get("name") or ""
                c["args"] += fn.get("arguments") or ""
        if cancel.is_set():
            raise Cancelled()
        msg = {"role": "assistant", "content": text or None}
        if calls:
            msg["tool_calls"] = [{"id": c["id"] or f"call_{i}", "type": "function",
                                  "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
                                 for i, c in sorted(calls.items())]
        history.append(msg)
        if not calls:
            return tail
        for tc in msg["tool_calls"]:
            try:
                args = json.loads(tc["function"]["arguments"])
                result = tools.dispatch(tc["function"]["name"], args if isinstance(args, dict) else {})
            except ValueError as e:
                result = f"ERRORE: argomenti JSON non validi: {e}"
            history.append({"role": "tool", "tool_call_id": tc["id"], "content": result})


# ---------------------------------------------------------------- main

def main():
    env = load_env()
    root = Path(os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")).resolve()
    md = root / "AGENT.md"
    console = Console()
    cancel = threading.Event()
    auto, show_reasoning, web = flag(env, "AGENT_AUTO"), flag(env, "AGENT_REASONING"), flag(env, "AGENT_WEBSEARCH")
    reasoning = ReasoningPrinter(show_reasoning)
    tools = Tools(root, console, not auto, cancel, reasoning.end, web)
    llm = LLM(env.get("LLM_URL", "http://localhost:11434"), env.get("LLM_KEY", "none"), env.get("LLM_MODEL", "deepseek-chat"))
    system = (f"Sei un coding agent. Lavori nella directory {root} (OS: {OS_NAME}, shell dei comandi: {'cmd' if WINDOWS else 'bash'}). "
              "Usa i tool per esplorare, leggere, creare e modificare file e per compilare (es. 'mvn -q compile'). "
              "Se la compilazione fallisce correggi e riprova. Rispondi in modo breve alla fine."
              + (" Se ti servono informazioni che non hai (documentazione, versioni di librerie, errori, API) "
                 "cercale su internet con webSearch e leggi le pagine utili con readWebPage." if web else "")
              + (f"\n\nIstruzioni del progetto (AGENT.md):\n{md.read_text(encoding='utf-8')}" if md.exists() else ""))
    memory = []  # cronologia senza system prompt
    print(f"Agent su {root} (exit per uscire{', ESC per interrompere' if console.can_interrupt() else ''}"
          f", AGENT_AUTO=true per non chiedere conferme{'' if web else ', AGENT_WEBSEARCH=true per la ricerca web'}"
          f"{'' if show_reasoning else ', AGENT_REASONING=true per vedere il reasoning'})")
    try:
        while True:
            q = console.read_line("\n> ")
            if q is None or q.strip().lower() == "exit":
                break
            q = q.strip()
            if not q:
                continue
            for _ in range(6):
                cancel.clear()
                tools.calls = 0
                work = memory + [{"role": "user", "content": q}]
                tail, error = "", None
                console.watch(cancel, llm.abort)

                full = [{"role": "system", "content": system}] + work
                try:
                    # run_turn aggiunge i messaggi in coda a `full`
                    tail = run_turn(llm, tools, full, reasoning, cancel)
                except Cancelled:
                    pass
                except Exception as e:  # abort durante lo stream = interruzione, altrimenti errore reale
                    if not cancel.is_set():
                        error = e
                finally:
                    console.stop_watching()
                had_reasoning, cancelled = reasoning.end(), cancel.is_set()
                if cancelled or error is None:
                    if not had_reasoning and not tail.endswith("\n"):
                        print()
                    if cancelled:
                        print("[interrotto]")
                    else:
                        memory[:] = full[1:]
                        trim(memory)
                    break
                limite = "429" in str(error)
                print("  rate limit, riprovo tra 15s..." if limite else f"Errore: {error}")
                if not limite:
                    break
                time.sleep(15)
    except KeyboardInterrupt:
        pass
    finally:
        console.restore()


if __name__ == "__main__":
    main()
