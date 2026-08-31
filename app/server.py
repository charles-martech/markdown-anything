"""Local web UI for the converter.

Starts a small HTTP server on 127.0.0.1, opens the page in the default browser,
and drives scripts/doc2gfm.py from clicks instead of terminal flags. Python
standard library only: no install step, no dependencies, no build.

    python3 app/server.py

It can also install what it needs: pandoc is fetched from its official GitHub
release into this app's own support folder, so a person never has to install
Homebrew, run a terminal command, or type an administrator password.

Nothing listens on a public interface, every request carries a token minted at
startup, and requests naming any host but this one are refused, so no other
page in the same browser can read that token or drive the converter.
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
# Downloads only ever come from GitHub's own hosts. The asset URL arrives
# inside an API response, so it is data from the network and is checked before
# anything is fetched from it.
DOWNLOAD_HOSTS = {
    "github.com", "api.github.com", "codeload.github.com",
    "objects.githubusercontent.com", "release-assets.githubusercontent.com",
    "raw.githubusercontent.com",
}
# The reader libraries, with upper bounds: a future major release cannot change
# what this app puts on someone's machine without a deliberate edit here.
READER_PACKAGES = [
    ("pymupdf4llm", "pymupdf4llm>=0.0.17,<2"),
    ("openpyxl", "openpyxl>=3.1,<4"),
]
# A request has to look like it came from this server's own page.
LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}
MAX_BODY = 1 << 20

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


def private_dir(path: Path) -> Path:
    """Create a directory only this user can open: it holds the app's token."""
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def note_request() -> None:
    global LAST_REQUEST
    LAST_REQUEST = time.monotonic()


def existing_instance() -> str | None:
    """URL of an instance already running for this user, if any."""
    try:
        saved = json.loads(PORT_FILE.read_text())
        url = f"http://127.0.0.1:{int(saved['port'])}/"
        request = urllib.request.Request(  # noqa: S310 - loopback URL built here
            f"{url}api/ping?token={saved['token']}")
        with urllib.request.urlopen(request, timeout=1.5) as response:  # noqa: S310
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

PROMPT_RE = re.compile(r"[^\w ,.:'()/-]")


def osascript(script: str, *arguments: str) -> subprocess.CompletedProcess:
    """Run AppleScript with its values passed in as arguments.

    Text is never pasted into the script itself. Anything that reached here
    from the page would otherwise be AppleScript for the machine to run, which
    is a whole computer's worth of trouble for the sake of a dialog title.
    """
    return subprocess.run(["osascript", "-e", script, *arguments],
                          capture_output=True, text=True)


def pick_folder(prompt: str) -> str:
    """Native folder chooser. Returns "" when cancelled or unavailable."""
    prompt = PROMPT_RE.sub(" ", str(prompt))[:120].strip() or "Choose a folder"
    if IS_MAC:
        proc = osascript(
            "on run argv\n"
            "  return POSIX path of (choose folder with prompt (item 1 of argv))\n"
            "end run", prompt)
        return proc.stdout.strip().rstrip("/") if proc.returncode == 0 else ""
    for tool, args in (
        ("zenity", ["--file-selection", "--directory", f"--title={prompt}"]),
        ("kdialog", ["--getexistingdirectory", str(Path.home())]),
    ):
        if shutil.which(tool):
            proc = subprocess.run([tool, *args], capture_output=True, text=True)
            return proc.stdout.strip() if proc.returncode == 0 else ""
    return ""


BUNDLE_SUFFIXES = {".app", ".pkg", ".command", ".workflow", ".scpt", ".dmg"}


def reveal(path: str) -> bool:
    """Show a path in the file manager.

    A file is revealed inside its folder rather than opened: `open` hands a
    document to whichever application claims it, and on a bundle it would run
    the bundle. Nothing here ever launches what it is pointed at.
    """
    if not isinstance(path, str) or not path:
        return False
    try:
        target = Path(path).expanduser().resolve(strict=True)
    except OSError:
        return False
    opener = shutil.which("open" if IS_MAC else "xdg-open")
    if not opener:
        return False
    plain_folder = (target.is_dir()
                    and target.suffix.lower() not in BUNDLE_SUFFIXES)
    if IS_MAC:
        command = [opener, str(target)] if plain_folder else [opener, "-R", str(target)]
    else:
        command = [opener, str(target if plain_folder else target.parent)]
    subprocess.Popen(command,
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


class UnsafeArchive(Exception):
    """A downloaded archive held a member pointing outside its own folder."""


def trusted_download(url: str) -> bool:
    """Only https, and only GitHub's own hosts."""
    parsed = urlparse(url)
    return (parsed.scheme == "https"
            and (parsed.hostname or "").lower() in DOWNLOAD_HOSTS)


def extract_archive(archive: Path, name: str, target: Path) -> None:
    """Unpack a download, writing only the files that belong inside `target`.

    An archive is untrusted input even when it came from a trusted host, so
    nothing here hands it to `extractall`: members are written one at a time,
    each to a path checked to be inside the folder, and links are not written
    at all. A link is what makes the check-then-extract pattern unsafe — an
    early symlink can move where a later member lands — and Pandoc does not
    need them: `bin/pandoc` is a real file, and `pandoc-lua` and
    `pandoc-server`, the two symlinks beside it, are not used by this app.
    """
    target.mkdir(parents=True, exist_ok=True)
    root = target.resolve()

    def destination(member: str) -> Path:
        """Where a member may be written, or UnsafeArchive if that is out."""
        parts = Path(member).parts
        if not member or member.startswith("/") or ".." in parts:
            raise UnsafeArchive(member)
        # normpath, not resolve: this must not follow anything on disk.
        chosen = Path(os.path.normpath(root / member))
        if chosen != root and root not in chosen.parents:
            raise UnsafeArchive(member)
        return chosen

    def write(source, path: Path, executable: bool) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as out:
            shutil.copyfileobj(source, out)
        if executable:
            path.chmod(0o755)

    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for entry in zf.infolist():
                path = destination(entry.filename)
                mode = (entry.external_attr >> 16) & 0xFFFF
                if entry.is_dir():
                    path.mkdir(parents=True, exist_ok=True)
                elif mode & 0o170000 == 0o120000:  # a symlink
                    continue
                else:
                    with zf.open(entry) as source:
                        write(source, path, bool(mode & 0o111))
        return
    with tarfile.open(archive) as tf:
        for member in tf.getmembers():
            path = destination(member.name)
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():   # links, devices, anything else
                continue
            source = tf.extractfile(member)
            if source is None:
                continue
            with source:
                write(source, path, bool(member.mode & 0o111))


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
        request = urllib.request.Request(  # noqa: S310 - constant https URL
            PANDOC_RELEASES, headers={"Accept": "application/vnd.github+json",
                                      "User-Agent": "document-to-markdown"})
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            release = json.load(response)
        for asset in release.get("assets", []):
            url = asset.get("browser_download_url", "")
            if pattern.search(asset.get("name", "")) and trusted_download(url):
                return url, asset["name"]
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
    if not trusted_download(url):
        _say(run, "Refusing to download Pandoc from an unexpected address.", "fail")
        return False
    _say(run, f"Downloading {name} (about 30 MB)...")
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mda-") as tmp:
        archive = Path(tmp) / name
        try:
            # trusted_download() above has already checked the scheme and host.
            request = urllib.request.Request(  # noqa: S310
                url, headers={"User-Agent": "document-to-markdown"})
            opened = urllib.request.urlopen(request, timeout=300)  # noqa: S310
            with opened as response, archive.open("wb") as out:
                shutil.copyfileobj(response, out)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            _say(run, f"Could not download Pandoc: {exc}", "fail")
            return False
        _say(run, "Unpacking...")
        target = Path(tmp) / "unpacked"
        try:
            extract_archive(archive, name, target)
        except UnsafeArchive as exc:
            _say(run, f"That Pandoc download tried to write outside its own "
                      f"folder ({exc}) and was discarded.", "fail")
            return False
        except (zipfile.BadZipFile, tarfile.TarError, OSError) as exc:
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


def install_python_packages(run: Run, packages: list[tuple[str, str]]) -> bool:
    """Install into our own folder, so the system Python is left untouched."""
    missing = [requirement for module, requirement in packages
               if not python_module_available(module)]
    if not missing:
        _say(run, "Reader libraries are already installed.", "ok")
        return True
    _say(run, f"Installing {', '.join(missing)}...")
    PYLIB_DIR.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--upgrade",
         "--no-input", "--disable-pip-version-check",
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
        ok = install_python_packages(INSTALL, READER_PACKAGES) and ok
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
    raw_source = options.get("source")
    if not isinstance(raw_source, str) or not raw_source.strip():
        return {"ok": False, "error": "Choose a folder or file to convert."}
    source = Path(raw_source).expanduser()
    if not source.exists():
        return {"ok": False, "error": "Choose a folder or file to convert."}
    output = options.get("output")
    if not isinstance(output, str) or not output.strip():
        output = default_output(str(source))
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
    only = options.get("only")
    only = only.strip() if isinstance(only, str) else ""
    for extension in re.split(r"[,\s]+", only):
        cleaned = extension.lstrip(".").lower()
        # An extension is letters and digits. Anything else is not a file type
        # anyone has, and has no business becoming part of a command line.
        if cleaned and re.fullmatch(r"[a-z0-9]{1,16}", cleaned):
            command += ["--include", cleaned]

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

    def _headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._headers()
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self, query: dict) -> bool:
        return secrets.compare_digest(
            (query.get("token") or [""])[0], TOKEN)

    def _from_own_page(self) -> bool:
        """True when the request came from this server's own page.

        The socket is loopback-only, which stops the network reaching it. This
        stops the other way in: a public web page whose domain is pointed at
        127.0.0.1 would otherwise count as the same origin as this app, and
        could read the token out of the page and drive the converter. Such a
        request carries that site's name in Host or Origin, so both are checked
        against the only names this server answers to.
        """
        host = (self.headers.get("Host") or "").strip()
        if host.startswith("["):                       # [::1]:port
            hostname = host[1:host.index("]")] if "]" in host else ""
        else:
            hostname = host.split(":", 1)[0]
        if hostname.lower() not in LOCAL_HOSTNAMES:
            return False
        origin = self.headers.get("Origin")
        if origin and (urlparse(origin).hostname or "").lower() not in LOCAL_HOSTNAMES:
            return False
        # Sent by every current browser; absent on curl and older ones.
        return self.headers.get("Sec-Fetch-Site") in (None, "same-origin", "none")

    @staticmethod
    def _cursor(query: dict) -> int:
        try:
            return max(0, int((query.get("cursor") or ["0"])[0]))
        except ValueError:
            return 0

    def do_GET(self) -> None:
        if not self._from_own_page():
            self._send({"error": "forbidden"}, 403)
            return
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
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'unsafe-inline'; "
                "style-src 'unsafe-inline'; connect-src 'self'; "
                "img-src 'self' data:; base-uri 'none'; form-action 'none'")
            self._headers()
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/favicon.ico":
            # The browser asks for this on its own, with no token to give. An
            # empty answer keeps a needless error out of the console.
            self.send_response(204)
            self._headers()
            self.end_headers()
            return
        if not self._authorized(query):
            self._send({"error": "unauthorized"}, 403)
            return
        if parsed.path == "/api/ping":
            self._send({"ok": True})
        elif parsed.path == "/api/engines":
            self._send({"engines": engine_status()})
        elif parsed.path == "/api/install-status":
            self._send(INSTALL.snapshot(self._cursor(query)))
        elif parsed.path == "/api/status":
            self._send(RUN.snapshot(self._cursor(query)))
        elif parsed.path == "/api/report":
            self._send(read_report())
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if not self._from_own_page():
            self._send({"error": "forbidden"}, 403)
            return
        note_request()
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self._authorized(query):
            self._send({"error": "unauthorized"}, 403)
            return
        try:
            length = max(0, int(self.headers.get("Content-Length") or 0))
        except ValueError:
            length = 0
        if length > MAX_BODY:
            self._send({"error": "request too large"}, 413)
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {}
        if not isinstance(payload, dict):
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
            expanded = (str(Path(folder).expanduser())
                        if isinstance(folder, str) and folder else "")
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
        private_dir(SUPPORT)
        # The token is the key to this server, so the file holding it is
        # created readable by nobody else, before anything is written into it.
        handle = os.open(PORT_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(json.dumps({"port": port, "token": TOKEN}))
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

    private_dir(SUPPORT)
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
        osascript('on run argv\n'
                  '  display dialog (item 1 of argv) buttons {"OK"} '
                  'default button 1 with icon caution '
                  'with title "Document to Markdown"\n'
                  'end run', message)
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
