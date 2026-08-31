"""Local web UI for the converter.

Starts a small HTTP server on 127.0.0.1, opens the page in the default browser,
and drives scripts/doc2gfm.py from clicks instead of terminal flags. Python
standard library only: no install step, no dependencies, no build.

    python3 app/server.py

It can also install what it needs: pandoc is fetched from its official GitHub
release into this app's own support folder, so a person never has to install
Homebrew, run a terminal command, or type an administrator password.

Nothing listens on a public interface and every request carries a token minted
at startup, so another page in the same browser cannot drive the converter.
"""

from __future__ import annotations

import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import time
import tempfile
import threading
import urllib.error
import urllib.request
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
CONVERTER = ROOT / "scripts" / "doc2gfm.py"
TOKEN = secrets.token_urlsafe(24)
IS_MAC = platform.system() == "Darwin"

# Everything this app installs for itself lives here, never on the system.
# Deleting this folder undoes every install the app has ever done.
_DEFAULT_SUPPORT = (Path.home() / "Library/Application Support/Document to Markdown"
                    if IS_MAC else Path.home() / ".local/share/document-to-markdown")
# DOC2MD_HOME relocates everything this app installs and remembers. The server
# it starts is a separate process, so this has to travel through the
# environment rather than a module global.
SUPPORT = Path(os.environ.get("DOC2MD_HOME") or _DEFAULT_SUPPORT)
BIN_DIR = SUPPORT / "bin"
PYLIB_DIR = SUPPORT / "python"
# An app launched from Finder inherits a bare PATH (/usr/bin:/bin:/usr/sbin:
# /sbin), not the PATH from a login shell, so tools installed by Homebrew are
# invisible unless we go looking. LibreOffice is never on any PATH: it lives
# inside its own application bundle.
EXTRA_TOOL_DIRS = [
    Path("/opt/homebrew/bin"), Path("/usr/local/bin"), Path("/opt/local/bin"),
    Path("/usr/bin"), Path("/bin"), Path.home() / ".local/bin",
    Path("/Applications/LibreOffice.app/Contents/MacOS"),
    Path.home() / "Applications/LibreOffice.app/Contents/MacOS",
]
PANDOC_RELEASES = "https://api.github.com/repos/jgm/pandoc/releases/latest"
PANDOC_FALLBACK = "3.1.11"
LIBREOFFICE_PAGE = "https://www.libreoffice.org/download/download-libreoffice/"

PROGRESS_RE = re.compile(r"^\[(\d+)/(\d+)\]\s+(ok|--|FAIL)\s+(.*)$")


class Run:
    """State of the conversion currently in flight, or the last one."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.process: subprocess.Popen | None = None
        self.lines: list[dict] = []
        self.done = 0
        self.total = 0
        self.finished = False
        self.exit_code: int | None = None
        self.output_dir = ""
        self.error = ""

    def reset(self, output_dir: str) -> None:
        with self.lock:
            self.process = None
            self.lines = []
            self.done = 0
            self.total = 0
            self.finished = False
            self.exit_code = None
            self.output_dir = output_dir
            self.error = ""

    def snapshot(self, since: int) -> dict:
        with self.lock:
            return {
                "lines": self.lines[since:],
                "cursor": len(self.lines),
                "done": self.done,
                "total": self.total,
                "finished": self.finished,
                "exitCode": self.exit_code,
                "outputDir": self.output_dir,
                "error": self.error,
                "running": self.process is not None and not self.finished,
            }


RUN = Run()

# The app is launched from an icon, with no terminal to close, so it tracks its
# own life: a second launch reuses the running one, the page can quit it, and an
# abandoned server stops on its own instead of lingering for days.
IDLE_TIMEOUT = 30 * 60
LAST_REQUEST = time.monotonic()
PORT_FILE = SUPPORT / "instance.json"


def note_request() -> None:
    global LAST_REQUEST
    LAST_REQUEST = time.monotonic()


def existing_instance() -> str | None:
    """URL of an instance already running for this user, if any."""
    try:
        saved = json.loads(PORT_FILE.read_text())
        url = f"http://127.0.0.1:{int(saved['port'])}/"
        request = urllib.request.Request(f"{url}api/ping?token={saved['token']}")
        with urllib.request.urlopen(request, timeout=1.5) as response:
            if response.status == 200:
                return url
    except Exception:  # noqa: BLE001 - any failure means "no usable instance"
        return None
    return None


def watchdog(server: ThreadingHTTPServer) -> None:
    while True:
        time.sleep(60)
        with RUN.lock:
            busy = RUN.process is not None and not RUN.finished
        with INSTALL.lock:
            busy = busy or (INSTALL.lines and not INSTALL.finished)
        if not busy and time.monotonic() - LAST_REQUEST > IDLE_TIMEOUT:
            server.shutdown()
            return


# --------------------------------------------------------------------------
# Tool discovery: the app's own bin folder counts as installed
# --------------------------------------------------------------------------

def tool_path(name: str) -> str | None:
    """Find a binary: our own copy, then PATH, then the usual Mac locations."""
    own = BIN_DIR / name
    if own.is_file() and os.access(own, os.X_OK):
        return str(own)
    found = shutil.which(name)
    if found:
        return found
    for directory in EXTRA_TOOL_DIRS:
        candidate = directory / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def search_path() -> str:
    """PATH for child processes: our bin, then everywhere a tool may live."""
    parts = [str(BIN_DIR)]
    parts += [str(d) for d in EXTRA_TOOL_DIRS if d.is_dir()]
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if entry and entry not in parts:
            parts.append(entry)
    return os.pathsep.join(parts)


def child_env() -> dict:
    """Environment for the converter: our bin folder and libraries come first."""
    env = dict(os.environ)
    env["PATH"] = search_path()
    if PYLIB_DIR.is_dir():
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (f"{PYLIB_DIR}{os.pathsep}{existing}" if existing
                             else str(PYLIB_DIR))
    return env


def python_module_available(name: str) -> bool:
    return subprocess.run([sys.executable, "-c", f"import {name}"],
                          capture_output=True, env=child_env()).returncode == 0


# --------------------------------------------------------------------------
# Folder picking and revealing, per platform
# --------------------------------------------------------------------------

def pick_folder(prompt: str) -> str:
    """Native folder chooser. Returns "" when cancelled or unavailable."""
    if IS_MAC:
        script = (f'POSIX path of (choose folder with prompt "{prompt}")')
        proc = subprocess.run(["osascript", "-e", script],
                              capture_output=True, text=True)
        return proc.stdout.strip().rstrip("/") if proc.returncode == 0 else ""
    for tool, args in (
        ("zenity", ["--file-selection", "--directory", f"--title={prompt}"]),
        ("kdialog", ["--getexistingdirectory", str(Path.home())]),
    ):
        if shutil.which(tool):
            proc = subprocess.run([tool, *args], capture_output=True, text=True)
            return proc.stdout.strip() if proc.returncode == 0 else ""
    return ""


def reveal(path: str) -> bool:
    target = Path(path).expanduser()
    if not target.exists():
        return False
    opener = "open" if IS_MAC else "xdg-open"
    if not shutil.which(opener):
        return False
    subprocess.Popen([opener, str(target)],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


# --------------------------------------------------------------------------
# Environment report: what is installed, and how to fix what is not
# --------------------------------------------------------------------------

def engine_status() -> list[dict]:
    """What each engine is for, whether it is here, and whether we can get it.

    `install` false means the app cannot fetch it silently (LibreOffice is a
    large signed installer), so the UI sends the person to a download page.
    """
    pdf_ready = python_module_available("pymupdf4llm") or bool(tool_path("pdftotext"))
    return [
        {"key": "pandoc", "name": "Pandoc", "required": True,
         "ok": bool(tool_path("pandoc")), "install": True,
         "purpose": "Word documents, web pages, ebooks, wikis and most other formats"},
        {"key": "pdf", "name": "PDF reader", "required": False,
         "ok": pdf_ready, "install": True,
         "purpose": "PDF files"},
        {"key": "excel", "name": "Spreadsheet reader", "required": False,
         "ok": python_module_available("openpyxl"), "install": True,
         "purpose": "Excel sheet names and headers"},
        {"key": "libreoffice", "name": "LibreOffice", "required": False,
         "ok": bool(tool_path("soffice") or tool_path("libreoffice")),
         "install": False, "link": LIBREOFFICE_PAGE,
         "purpose": "older Word, PowerPoint and Excel files, Pages and Keynote"},
    ]


def count_candidates(folder: str) -> dict:
    """Rough preview of what a folder holds, for the confirmation line."""
    root = Path(folder).expanduser()
    if not root.is_dir():
        return {"files": 0, "kinds": []}
    kinds: dict[str, int] = {}
    total = 0
    for path in root.rglob("*"):
        if path.is_file() and not path.name.startswith("."):
            total += 1
            kinds[path.suffix.lower() or "(no extension)"] = kinds.get(
                path.suffix.lower() or "(no extension)", 0) + 1
    top = sorted(kinds.items(), key=lambda kv: -kv[1])[:6]
    return {"files": total, "kinds": [{"ext": k, "count": v} for k, v in top]}


# --------------------------------------------------------------------------
# Installing the engines, without Homebrew and without a password
# --------------------------------------------------------------------------

INSTALL = Run()


def _say(run: Run, text: str, state: str = "info") -> None:
    with run.lock:
        run.lines.append({"state": state, "text": text})


def pandoc_asset() -> tuple[str, str]:
    """URL of the official pandoc build for this machine, and its file name."""
    machine = platform.machine().lower()
    if IS_MAC:
        arch = "arm64" if machine in ("arm64", "aarch64") else "x86_64"
        pattern = re.compile(rf"pandoc-([\d.]+)-{arch}-macOS\.zip$")
    else:
        arch = "arm64" if machine in ("arm64", "aarch64") else "amd64"
        pattern = re.compile(rf"pandoc-([\d.]+)-linux-{arch}\.tar\.gz$")
    try:
        request = urllib.request.Request(
            PANDOC_RELEASES, headers={"Accept": "application/vnd.github+json",
                                      "User-Agent": "document-to-markdown"})
        with urllib.request.urlopen(request, timeout=30) as response:
            release = json.load(response)
        for asset in release.get("assets", []):
            if pattern.search(asset.get("name", "")):
                return asset["browser_download_url"], asset["name"]
    except (urllib.error.URLError, ValueError, KeyError, TimeoutError):
        pass  # rate limited or offline: fall back to a known-good version
    version = PANDOC_FALLBACK
    name = (f"pandoc-{version}-{arch}-macOS.zip" if IS_MAC
            else f"pandoc-{version}-linux-{arch}.tar.gz")
    return (f"https://github.com/jgm/pandoc/releases/download/{version}/{name}", name)


def install_pandoc(run: Run) -> bool:
    if tool_path("pandoc"):
        _say(run, "Pandoc is already installed.", "ok")
        return True
    url, name = pandoc_asset()
    _say(run, f"Downloading {name} (about 30 MB)...")
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mda-") as tmp:
        archive = Path(tmp) / name
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "document-to-markdown"})
            with urllib.request.urlopen(request, timeout=300) as response, \
                    archive.open("wb") as out:
                shutil.copyfileobj(response, out)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            _say(run, f"Could not download Pandoc: {exc}", "fail")
            return False
        _say(run, "Unpacking...")
        target = Path(tmp) / "unpacked"
        try:
            if name.endswith(".zip"):
                with zipfile.ZipFile(archive) as zf:
                    zf.extractall(target)
            else:
                with tarfile.open(archive) as tf:
                    tf.extractall(target)
        except (zipfile.BadZipFile, tarfile.TarError) as exc:
            _say(run, f"The download was damaged: {exc}", "fail")
            return False
        found = next((p for p in target.rglob("pandoc")
                      if p.is_file() and p.parent.name == "bin"), None)
        if found is None:
            _say(run, "That Pandoc build did not contain the program.", "fail")
            return False
        destination = BIN_DIR / "pandoc"
        shutil.copy2(found, destination)
        destination.chmod(0o755)
    if IS_MAC:
        # Downloads carry a quarantine flag; clear it on the file we placed.
        subprocess.run(["xattr", "-d", "com.apple.quarantine", str(BIN_DIR / "pandoc")],
                       capture_output=True)
    version = subprocess.run([str(BIN_DIR / "pandoc"), "--version"],
                             capture_output=True, text=True)
    if version.returncode != 0:
        _say(run, "Pandoc was installed but will not run on this Mac.", "fail")
        return False
    _say(run, f"Installed {version.stdout.splitlines()[0]}", "ok")
    return True


def install_python_packages(run: Run, packages: list[str]) -> bool:
    """Install into our own folder, so the system Python is left untouched."""
    missing = [p for p in packages
               if not python_module_available(p.replace("-", "_"))]
    if not missing:
        _say(run, "Reader libraries are already installed.", "ok")
        return True
    _say(run, f"Installing {', '.join(missing)}...")
    PYLIB_DIR.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--upgrade",
         "--target", str(PYLIB_DIR), *missing],
        capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()
        _say(run, "Could not install the reader libraries: "
                  + (tail[-1][:200] if tail else "unknown error"), "fail")
        return False
    _say(run, f"Installed {', '.join(missing)}", "ok")
    return True


def start_install() -> dict:
    with INSTALL.lock:
        if INSTALL.process is None and not INSTALL.finished and INSTALL.lines:
            return {"ok": False, "error": "Setup is already running."}
    INSTALL.reset("")

    def worker() -> None:
        ok = install_pandoc(INSTALL)
        # PyMuPDF reads PDFs and openpyxl reads Excel. Neither is required for
        # the app to be useful, so a failure here is reported, not fatal.
        ok = install_python_packages(INSTALL, ["pymupdf4llm", "openpyxl"]) and ok
        with INSTALL.lock:
            INSTALL.finished = True
            INSTALL.exit_code = 0 if ok else 1
        _say(INSTALL, "Setup finished." if ok
             else "Setup finished, but something did not install.",
             "ok" if ok else "fail")

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True}


# --------------------------------------------------------------------------
# Running a conversion
# --------------------------------------------------------------------------

def start_conversion(options: dict) -> dict:
    source = Path(options.get("source", "")).expanduser()
    if not source.exists():
        return {"ok": False, "error": "Choose a folder or file to convert."}
    output = options.get("output") or default_output(str(source))
    output_path = Path(output).expanduser()
    if source.is_dir() and (output_path == source
                            or str(output_path).startswith(str(source) + os.sep)):
        return {"ok": False,
                "error": "The output folder cannot sit inside the folder being "
                         "converted. Pick a different destination."}
    with RUN.lock:
        if RUN.process is not None and RUN.process.poll() is None:
            return {"ok": False, "error": "A conversion is already running."}

    command = [sys.executable, "-u", str(CONVERTER), str(source),
               "-o", str(output_path)]
    if options.get("flat"):
        command.append("--flat")
    if options.get("force"):
        command.append("--force")
    if not options.get("media", True):
        command.append("--no-media")
    if options.get("noEmDash"):
        command.append("--no-em-dash")
    if options.get("ocr"):
        command.append("--ocr")
    only = (options.get("only") or "").strip()
    for extension in re.split(r"[,\s]+", only):
        if extension:
            command += ["--include", extension.lstrip(".")]

    RUN.reset(str(output_path))
    thread = threading.Thread(target=_run, args=(command,), daemon=True)
    thread.start()
    return {"ok": True, "output": str(output_path)}


def _run(command: list[str]) -> None:
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True,
                                   bufsize=1, env=child_env())
    except OSError as exc:
        with RUN.lock:
            RUN.error = f"Could not start the converter: {exc}"
            RUN.finished = True
            RUN.exit_code = -1
        return
    with RUN.lock:
        RUN.process = process
    for raw in process.stdout:  # type: ignore[union-attr]
        line = raw.rstrip("\n")
        match = PROGRESS_RE.match(line)
        with RUN.lock:
            if match:
                RUN.done = int(match.group(1))
                RUN.total = int(match.group(2))
                state = {"ok": "ok", "--": "skip", "FAIL": "fail"}[match.group(3)]
                RUN.lines.append({"state": state, "text": match.group(4)})
            elif line.strip():
                RUN.lines.append({"state": "info", "text": line})
    process.wait()
    with RUN.lock:
        RUN.finished = True
        RUN.exit_code = process.returncode


def cancel() -> None:
    with RUN.lock:
        process = RUN.process
    if process and process.poll() is None:
        process.terminate()


def default_output(source: str) -> str:
    path = Path(source).expanduser()
    base = path if path.is_dir() else path.parent
    return str(base.parent / f"{base.name}-markdown")


def read_report() -> dict:
    with RUN.lock:
        output = RUN.output_dir
    report = Path(output) / "_conversion-report.md"
    if not report.exists():
        return {"report": ""}
    return {"report": report.read_text(encoding="utf-8")[:200_000]}


# --------------------------------------------------------------------------
# HTTP plumbing
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "doc2gfm-ui"

    def log_message(self, *args) -> None:  # keep the terminal quiet
        pass

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self, query: dict) -> bool:
        return secrets.compare_digest(
            (query.get("token") or [""])[0], TOKEN)

    def do_GET(self) -> None:
        note_request()
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path in ("/", "/index.html"):
            page = (APP_DIR / "index.html").read_text(encoding="utf-8")
            page = page.replace("__TOKEN__", TOKEN)
            body = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if not self._authorized(query):
            self._send({"error": "unauthorized"}, 403)
            return
        if parsed.path == "/api/ping":
            self._send({"ok": True})
        elif parsed.path == "/api/engines":
            self._send({"engines": engine_status()})
        elif parsed.path == "/api/install-status":
            since = int((query.get("cursor") or ["0"])[0])
            self._send(INSTALL.snapshot(since))
        elif parsed.path == "/api/status":
            since = int((query.get("cursor") or ["0"])[0])
            self._send(RUN.snapshot(since))
        elif parsed.path == "/api/report":
            self._send(read_report())
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self) -> None:
        note_request()
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self._authorized(query):
            self._send({"error": "unauthorized"}, 403)
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            payload = {}
        if parsed.path == "/api/pick":
            folder = pick_folder(payload.get("prompt", "Choose a folder"))
            result = {"path": folder}
            if folder:
                result.update(count_candidates(folder))
                result["suggestedOutput"] = default_output(folder)
            self._send(result)
        elif parsed.path == "/api/inspect":
            folder = payload.get("path", "")
            expanded = str(Path(folder).expanduser()) if folder else ""
            result = {"path": expanded, "exists": bool(expanded)
                      and Path(expanded).exists()}
            if result["exists"]:
                result.update(count_candidates(expanded))
                result["suggestedOutput"] = default_output(expanded)
            self._send(result)
        elif parsed.path == "/api/install":
            self._send(start_install())
        elif parsed.path == "/api/convert":
            self._send(start_conversion(payload))
        elif parsed.path == "/api/cancel":
            cancel()
            self._send({"ok": True})
        elif parsed.path == "/api/reveal":
            self._send({"ok": reveal(payload.get("path", ""))})
        elif parsed.path == "/api/quit":
            self._send({"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self._send({"error": "not found"}, 404)


def serve() -> int:
    """The long-lived part: hold the port and answer the page."""
    if not CONVERTER.exists():
        print(f"Converter not found at {CONVERTER}", file=sys.stderr)
        return 2
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    try:
        SUPPORT.mkdir(parents=True, exist_ok=True)
        PORT_FILE.write_text(json.dumps({"port": port, "token": TOKEN}))
    except OSError:
        pass
    print(f"Document to Markdown is running at http://127.0.0.1:{port}/")
    print("Close the page and quit from there, or press Ctrl+C here.")
    threading.Thread(target=watchdog, args=(server,), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        PORT_FILE.unlink(missing_ok=True)
    return 0


def launch() -> int:
    """The part the app icon runs: open the page and get out of the way.

    macOS keeps an app in its bouncing "launching" state until the process it
    started finishes launching. A plain script app never registers with the
    window server, so if this process stayed alive to serve HTTP, the icon
    would bounce forever and a second click would do nothing. Instead the
    server is started detached and this process exits within a second, which
    also means every later click on the icon lands here again and simply
    reopens the page of the server that is already running.
    """
    running = existing_instance()
    if running:
        webbrowser.open(running)
        return 0

    SUPPORT.mkdir(parents=True, exist_ok=True)
    log = SUPPORT / "log.txt"
    with log.open("ab") as handle:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--serve"],
                         stdout=handle, stderr=handle, stdin=subprocess.DEVNULL,
                         start_new_session=True, cwd=str(ROOT))

    for _ in range(100):  # up to ten seconds for the server to claim a port
        time.sleep(0.1)
        started = existing_instance()
        if started:
            webbrowser.open(started)
            return 0
    _dialog("Document to Markdown could not start.\n\nThe details are in:\n"
            f"{log}")
    return 1


def _dialog(message: str) -> None:
    if IS_MAC:
        subprocess.run(["osascript", "-e",
                        f'display dialog "{message}" buttons {{"OK"}} '
                        'default button 1 with icon caution '
                        'with title "Document to Markdown"'],
                       capture_output=True)
    else:
        print(message, file=sys.stderr)


def main() -> int:
    if "--serve" in sys.argv:
        return serve()
    if "--no-browser" in sys.argv:      # tests and terminal use
        return serve()
    return launch()


if __name__ == "__main__":
    sys.exit(main())
