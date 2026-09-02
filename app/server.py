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

import base64
import json
import os
import platform
import re
import secrets
import shutil
import socketserver
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
IS_WINDOWS = platform.system() == "Windows"
PLATFORM = "mac" if IS_MAC else "windows" if IS_WINDOWS else "linux"
FILE_MANAGER = {"mac": "Finder", "windows": "File Explorer",
                "linux": "your file manager"}[PLATFORM]


def default_support_dir() -> Path:
    """The app's own folder on this platform.

    scripts/doc2gfm.py works this out the same way, so that a converter run
    from a terminal or by an AI assistant finds the engines the app installed.
    Change one and change the other.
    """
    if IS_MAC:
        return Path.home() / "Library/Application Support/Document to Markdown"
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData/Local")
        return Path(base) / "Document to Markdown"
    return Path.home() / ".local/share/document-to-markdown"


# Everything this app installs for itself lives here, never on the system.
# Deleting this folder undoes every install the app has ever done.
# DOC2MD_HOME relocates everything this app installs and remembers. The server
# it starts is a separate process, so this has to travel through the
# environment rather than a module global.
SUPPORT = Path(os.environ.get("DOC2MD_HOME") or default_support_dir())
BIN_DIR = SUPPORT / "bin"
PYLIB_DIR = SUPPORT / "python"
# An app launched from Finder inherits a bare PATH (/usr/bin:/bin:/usr/sbin:
# /sbin), not the PATH from a login shell, so tools installed by Homebrew are
# invisible unless we go looking. LibreOffice is never on any PATH: it lives
# inside its own application bundle, on every platform.
if IS_WINDOWS:
    EXTRA_TOOL_DIRS = []
    for _base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                  os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                  os.environ.get("LOCALAPPDATA", "")):
        if _base:
            EXTRA_TOOL_DIRS += [Path(_base) / "Pandoc",
                                Path(_base) / "LibreOffice" / "program",
                                Path(_base) / "poppler" / "Library" / "bin"]
else:
    EXTRA_TOOL_DIRS = [
        Path("/opt/homebrew/bin"), Path("/usr/local/bin"), Path("/opt/local/bin"),
        Path("/usr/bin"), Path("/bin"), Path.home() / ".local/bin",
        Path("/Applications/LibreOffice.app/Contents/MacOS"),
        Path.home() / "Applications/LibreOffice.app/Contents/MacOS",
        Path("/usr/lib/libreoffice/program"), Path("/opt/libreoffice/program"),
        Path("/snap/bin"),
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

REPO = "charles-martech/markdown-anything"
RELEASES_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
# An update installs a whole copy of the app's files here and the launcher
# prefers it, so an update never rewrites the bundle in /Applications. Deleting
# this folder puts back the version that shipped in the bundle.
PAYLOAD_DIR = SUPPORT / "current"
# Whether to look for updates without being asked. Unset until the person has
# been asked once and answered; the app checks nothing until they have.
SETTINGS_FILE = SUPPORT / "settings.json"
CHECK_INTERVAL = 24 * 60 * 60
# A release tag is text from the network. It is only ever a version number.
TAG_RE = re.compile(r"v?\d+(?:\.\d+){0,3}")

PROGRESS_RE = re.compile(r"^\[(\d+)/(\d+)\]\s+(ok|--|FAIL)\s+(.*)$")


def read_stamp(name: str, default: str) -> str:
    """A one-line file written beside the app's own files at build time."""
    try:
        return (ROOT / name).read_text(encoding="utf-8").strip() or default
    except OSError:
        return default


VERSION = read_stamp("VERSION", "0.0.0")
# The parts of the bundle an update cannot replace: the launcher, Info.plist
# and the icon. A release needing a newer one needs the installer, not us.
BUNDLE_FORMAT = read_stamp("BUNDLE_FORMAT", "1")


def version_tuple(text: str) -> tuple:
    return tuple(int(part) for part in re.findall(r"\d+", text)[:4])


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
        # A filesystem that does not do Unix permissions, or a folder owned by
        # somebody else. Neither is worth refusing to start over: the token
        # file below is created with its own permissions regardless.
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
        with UPDATE.lock:
            busy = busy or (UPDATE.lines and not UPDATE.finished)
        if not busy and time.monotonic() - LAST_REQUEST > IDLE_TIMEOUT:
            server.shutdown()
            return


# --------------------------------------------------------------------------
# Tool discovery: the app's own bin folder counts as installed
# --------------------------------------------------------------------------

def tool_path(name: str) -> str | None:
    """Find a binary: our own copy, then PATH, then the usual locations."""
    names = [name, name + ".exe"] if IS_WINDOWS else [name]
    for candidate in [BIN_DIR / n for n in names]:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    found = shutil.which(name)
    if found:
        return found
    for directory in EXTRA_TOOL_DIRS:
        for candidate in [directory / n for n in names]:
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
    # The converter prints file names. An app opened from an icon can have no
    # locale at all, and a name the locale cannot encode must not crash the
    # run that is converting it.
    env["PYTHONIOENCODING"] = "utf-8"
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


def powershell_dialog(script: str, prompt: str) -> list[str]:
    """Run a Windows Forms dialog from PowerShell and return the chosen paths.

    The script is fixed text handed over base64-encoded, so no quoting rule of
    PowerShell's or cmd's ever applies to it, and the prompt travels in an
    environment variable rather than inside the script, for the same reason
    AppleScript gets its text as an argument: nothing from the page is ever
    part of a program.
    """
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", encoded],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env={**os.environ, "MDA_PROMPT": prompt},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError:
        return []
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


# Output as UTF-8 so a path with an accent survives the trip; a form that is
# topmost, so the dialog opens in front of the browser instead of behind it.
_PS_PRELUDE = (
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
    "Add-Type -AssemblyName System.Windows.Forms\n"
    "$owner = New-Object System.Windows.Forms.Form\n"
    "$owner.TopMost = $true\n")
_PS_FOLDER = _PS_PRELUDE + (
    "$d = New-Object System.Windows.Forms.FolderBrowserDialog\n"
    "$d.Description = $env:MDA_PROMPT\n"
    "$d.ShowNewFolderButton = $true\n"
    "if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) "
    "{ Write-Output $d.SelectedPath }\n")
_PS_FILES = _PS_PRELUDE + (
    "$d = New-Object System.Windows.Forms.OpenFileDialog\n"
    "$d.Title = $env:MDA_PROMPT\n"
    "$d.Multiselect = $true\n"
    "$d.Filter = 'All files (*.*)|*.*'\n"
    "if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) "
    "{ $d.FileNames | ForEach-Object { Write-Output $_ } }\n")


def _linux_dialog(zenity_args: list[str], kdialog_args: list[str]) -> list[str]:
    for tool, args in (("zenity", zenity_args), ("kdialog", kdialog_args)):
        if shutil.which(tool):
            proc = subprocess.run([tool, *args], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
            if proc.returncode != 0:
                return []
            return [line for line in proc.stdout.splitlines() if line.strip()]
    return []


def pick_folder(prompt: str) -> str:
    """Native folder chooser. Returns "" when cancelled or unavailable."""
    prompt = PROMPT_RE.sub(" ", str(prompt))[:120].strip() or "Choose a folder"
    if IS_MAC:
        proc = osascript(
            "on run argv\n"
            "  return POSIX path of (choose folder with prompt (item 1 of argv))\n"
            "end run", prompt)
        return proc.stdout.strip().rstrip("/") if proc.returncode == 0 else ""
    if IS_WINDOWS:
        chosen = powershell_dialog(_PS_FOLDER, prompt)
    else:
        chosen = _linux_dialog(
            ["--file-selection", "--directory", f"--title={prompt}"],
            ["--getexistingdirectory", str(Path.home()), "--title", prompt])
    return chosen[0].rstrip("/") if chosen else ""


def pick_files(prompt: str) -> list[str]:
    """Native file chooser, several files allowed. [] when cancelled."""
    prompt = PROMPT_RE.sub(" ", str(prompt))[:120].strip() or "Choose files"
    if IS_MAC:
        # `choose file` returns a list of aliases; POSIX path works on one
        # alias at a time, so they are walked and joined with newlines. A
        # file name holding a newline is possible on a Mac and not something
        # anyone has, and the worst case is that one path does not exist.
        proc = osascript(
            "on run argv\n"
            "  set chosen to choose file with prompt (item 1 of argv) "
            "with multiple selections allowed\n"
            '  set out to ""\n'
            "  repeat with f in chosen\n"
            "    set out to out & POSIX path of f & linefeed\n"
            "  end repeat\n"
            "  return out\n"
            "end run", prompt)
        if proc.returncode != 0:
            return []
        return [line for line in proc.stdout.splitlines() if line.strip()]
    if IS_WINDOWS:
        return powershell_dialog(_PS_FILES, prompt)
    return _linux_dialog(
        ["--file-selection", "--multiple", "--separator=\n", f"--title={prompt}"],
        ["--getopenfilename", str(Path.home()), "--multiple", "--separator", "\n",
         "--title", prompt])


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
    plain_folder = (target.is_dir()
                    and target.suffix.lower() not in BUNDLE_SUFFIXES)
    if IS_WINDOWS:
        # Explorer takes "/select,PATH" as one argument. The path came back
        # from resolve(strict=True), so it exists and, on Windows, cannot
        # hold a quote; nothing here is a shell.
        command = (["explorer", str(target)] if plain_folder
                   else ["explorer", f"/select,{target}"])
    else:
        opener = shutil.which("open" if IS_MAC else "xdg-open")
        if not opener:
            return False
        if IS_MAC:
            command = ([opener, str(target)] if plain_folder
                       else [opener, "-R", str(target)])
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


PREVIEW_LIMIT = 50_000


def count_candidates(paths: list[str]) -> dict:
    """Rough preview of what a choice holds, for the confirmation line.

    Files are counted as themselves; folders are walked the way the converter
    walks them, leaving out dot-folders and the two folders nobody means. A
    very large tree stops being counted at PREVIEW_LIMIT so that choosing a
    home folder does not leave the page waiting on a full walk of it.
    """
    kinds: dict[str, int] = {}
    total = 0
    capped = False

    def note(path: Path) -> None:
        ext = path.suffix.lower() or "(no extension)"
        kinds[ext] = kinds.get(ext, 0) + 1

    for raw in paths:
        item = Path(raw).expanduser()
        if item.is_file():
            total += 1
            note(item)
            continue
        if not item.is_dir():
            continue
        for root, dirs, files in os.walk(item):
            dirs[:] = [d for d in dirs if not d.startswith(".")
                       and d not in {"node_modules", "__pycache__"}]
            for name in files:
                if name.startswith("."):
                    continue
                total += 1
                note(Path(root) / name)
                if total >= PREVIEW_LIMIT:
                    capped = True
                    break
            if capped:
                break
        if capped:
            break
    top = sorted(kinds.items(), key=lambda kv: -kv[1])[:6]
    return {"files": total, "capped": capped,
            "kinds": [{"ext": k, "count": v} for k, v in top]}


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
        suffix = f"{arch}-macOS.zip"
    elif IS_WINDOWS:
        # Pandoc ships one Windows build; an ARM machine runs it emulated.
        suffix = "windows-x86_64.zip"
    else:
        arch = "arm64" if machine in ("arm64", "aarch64") else "amd64"
        suffix = f"linux-{arch}.tar.gz"
    pattern = re.compile(rf"pandoc-([\d.]+)-{re.escape(suffix)}$")
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
    name = f"pandoc-{version}-{suffix}"
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
        # The Mac and Linux archives keep the program in bin/; the Windows
        # one keeps pandoc.exe at the top of its folder.
        program = "pandoc.exe" if IS_WINDOWS else "pandoc"
        found = next((p for p in target.rglob(program)
                      if p.is_file() and (IS_WINDOWS or p.parent.name == "bin")),
                     None)
        if found is None:
            _say(run, "That Pandoc build did not contain the program.", "fail")
            return False
        destination = BIN_DIR / program
        shutil.copy2(found, destination)
        destination.chmod(0o755)
    if IS_MAC:
        # Downloads carry a quarantine flag; clear it on the file we placed.
        subprocess.run(["xattr", "-d", "com.apple.quarantine", str(destination)],
                       capture_output=True)
    version = subprocess.run([str(destination), "--version"],
                             capture_output=True, text=True)
    if version.returncode != 0:
        _say(run, "Pandoc was installed but will not run on this computer.",
             "fail")
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
# Updating the app, when the person asks
# --------------------------------------------------------------------------
#
# Nothing here runs on its own. The app makes no network request that was not
# asked for by a click, which is the same promise the setup step keeps, and the
# reason there is a button rather than a check on every launch.

UPDATE = Run()


def read_settings() -> dict:
    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return saved if isinstance(saved, dict) else {}


def write_settings(values: dict) -> None:
    try:
        private_dir(SUPPORT)
        handle = os.open(SETTINGS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                         0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(json.dumps(values))
    except OSError:
        # A full or read-only disk. The answer is not worth refusing to run
        # over: the app asks again next time, which is the safe way to be
        # wrong — it never checks the network on an answer it does not have.
        pass


def settings_for_page() -> dict:
    """What the page needs to know: the answer, and whether it has one.

    `autoCheck` is None until the person has chosen, which is what makes the
    page ask rather than assume. Assuming either way would be answering a
    question about their privacy on their behalf.
    """
    saved = read_settings()
    auto = saved.get("autoCheck")
    try:
        last = float(saved.get("lastCheck") or 0)
    except (TypeError, ValueError):
        last = 0.0
    due = (time.time() - last) > CHECK_INTERVAL
    return {"version": VERSION,
            "platform": PLATFORM,
            "fileManager": FILE_MANAGER,
            "examplePath": str(Path.home() / "Documents" / "report.pdf"),
            "autoCheck": auto if isinstance(auto, bool) else None,
            "checkNow": auto is True and due}


def set_auto_check(enabled: bool) -> dict:
    saved = read_settings()
    saved["autoCheck"] = enabled
    write_settings(saved)
    return {"ok": True, "autoCheck": enabled}


def note_check() -> None:
    saved = read_settings()
    saved["lastCheck"] = time.time()
    write_settings(saved)


def latest_release() -> dict:
    """The newest release on GitHub, or {} if it cannot be asked."""
    try:
        request = urllib.request.Request(  # noqa: S310 - constant https URL
            RELEASES_LATEST, headers={"Accept": "application/vnd.github+json",
                                      "User-Agent": "document-to-markdown"})
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            release = json.load(response)
    except (urllib.error.URLError, ValueError, TimeoutError, OSError):
        return {}
    tag = str(release.get("tag_name") or "")
    if not TAG_RE.fullmatch(tag):
        return {}   # not a version number; nothing we are willing to fetch
    return {"tag": tag, "url": str(release.get("html_url") or ""),
            "name": str(release.get("name") or tag)}


def update_status() -> dict:
    """What the page shows when it has asked GitHub for the newest version."""
    note_check()
    release = latest_release()
    if not release:
        return {"ok": False,
                "error": "Could not reach GitHub to ask what the newest "
                         "version is. Check your connection and try again."}
    newer = version_tuple(release["tag"]) > version_tuple(VERSION)
    return {"ok": True, "current": VERSION, "latest": release["tag"],
            "newer": newer, "url": release["url"], "name": release["name"]}


def payload_root(unpacked: Path) -> Path:
    """The folder inside a release archive that holds the app's own files.

    A GitHub source archive wraps everything in one directory named after the
    tag, so this looks one level down, and then checks that what it found is
    actually this app rather than trusting the shape of the download.
    """
    candidates = [unpacked, *[p for p in unpacked.iterdir() if p.is_dir()]]
    for candidate in candidates:
        if all((candidate / part).is_file() for part in
               ("app/server.py", "app/index.html", "scripts/doc2gfm.py",
                "VERSION", "BUNDLE_FORMAT")):
            return candidate
    raise UnsafeArchive("that download does not contain the app's files")


def preflight(root: Path) -> tuple[bool, str]:
    """Start the downloaded server, once, before trusting it with the app.

    A release that cannot start would otherwise leave the icon doing nothing
    and no obvious way back, so it is run here first and discarded if it fails.
    """
    with tempfile.TemporaryDirectory(prefix="mda-check-") as tmp:
        env = dict(os.environ, DOC2MD_HOME=tmp)
        try:
            result = subprocess.run(
                [sys.executable, str(root / "app" / "server.py"), "--selftest"],
                capture_output=True, text=True, timeout=120, env=env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return False, detail[-1][:200] if detail else f"exit {result.returncode}"
    return True, ""


def install_payload(root: Path) -> None:
    """Put a checked copy of the app's files where the launcher looks.

    The swap is two renames inside one folder, so a version that failed to copy
    never becomes the one that runs.
    """
    private_dir(SUPPORT)
    staging = SUPPORT / "current.new"
    previous = SUPPORT / "current.old"
    for path in (staging, previous):
        shutil.rmtree(path, ignore_errors=True)
    shutil.copytree(root, staging)
    if PAYLOAD_DIR.exists():
        os.replace(PAYLOAD_DIR, previous)
    os.replace(staging, PAYLOAD_DIR)
    shutil.rmtree(previous, ignore_errors=True)


def start_update() -> dict:
    with UPDATE.lock:
        if UPDATE.lines and not UPDATE.finished:
            return {"ok": False, "error": "An update is already running."}
    UPDATE.reset("")

    def worker() -> None:
        ok = run_update(UPDATE)
        with UPDATE.lock:
            UPDATE.finished = True
            UPDATE.exit_code = 0 if ok else 1

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True}


def run_update(run: Run) -> bool:
    release = latest_release()
    if not release:
        _say(run, "Could not reach GitHub to ask for the newest version.", "fail")
        return False
    tag = release["tag"]
    if version_tuple(tag) <= version_tuple(VERSION):
        _say(run, f"Already up to date ({VERSION}).", "ok")
        return True
    url = f"https://github.com/{REPO}/archive/refs/tags/{tag}.tar.gz"
    if not trusted_download(url):
        _say(run, "Refusing to download from an unexpected address.", "fail")
        return False
    _say(run, f"Downloading {tag}...")
    with tempfile.TemporaryDirectory(prefix="mda-update-") as tmp:
        archive = Path(tmp) / "release.tar.gz"
        try:
            request = urllib.request.Request(  # noqa: S310 - checked above
                url, headers={"User-Agent": "document-to-markdown"})
            opened = urllib.request.urlopen(request, timeout=300)  # noqa: S310
            with opened as response, archive.open("wb") as out:
                shutil.copyfileobj(response, out)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            _say(run, f"Could not download the update: {exc}", "fail")
            return False
        _say(run, "Unpacking...")
        unpacked = Path(tmp) / "unpacked"
        try:
            extract_archive(archive, "release.tar.gz", unpacked)
            root = payload_root(unpacked)
        except UnsafeArchive as exc:
            _say(run, f"That download was refused: {exc}", "fail")
            return False
        except (tarfile.TarError, OSError) as exc:
            _say(run, f"The download was damaged: {exc}", "fail")
            return False
        wanted = read_stamp_from(root, "BUNDLE_FORMAT", "1")
        if version_tuple(wanted) > version_tuple(BUNDLE_FORMAT):
            _say(run, f"{tag} changes parts of the app that an update cannot "
                      "replace. Run the install line again to get it:", "fail")
            _say(run, "curl -fsSL https://raw.githubusercontent.com/"
                      f"{REPO}/main/install.sh | bash", "fail")
            return False
        _say(run, "Checking that it runs...")
        started, detail = preflight(root)
        if not started:
            _say(run, f"{tag} did not start on this computer, so nothing was "
                      f"changed: {detail}", "fail")
            return False
        try:
            install_payload(root)
        except OSError as exc:
            _say(run, f"Could not install the update: {exc}", "fail")
            return False
    _say(run, f"Updated to {tag}. Quit the app and open it again to use it.",
         "ok")
    return True


def read_stamp_from(root: Path, name: str, default: str) -> str:
    try:
        return (root / name).read_text(encoding="utf-8").strip() or default
    except OSError:
        return default


# --------------------------------------------------------------------------
# Running a conversion
# --------------------------------------------------------------------------

def requested_sources(options: dict) -> list[Path]:
    """The files and folders the page asked for, existing ones only.

    The page sends `sources`, a list; `source`, a single string, is what it
    sent before files could be chosen one at a time, and still works.
    """
    raw = options.get("sources")
    if not isinstance(raw, list):
        raw = [options.get("source")]
    sources: list[Path] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            continue
        path = Path(item).expanduser()
        if path.exists() and path not in sources:
            sources.append(path)
    return sources


def start_conversion(options: dict) -> dict:
    sources = requested_sources(options)
    if not sources:
        return {"ok": False, "error": "Choose a folder or some files to convert."}
    output = options.get("output")
    if not isinstance(output, str) or not output.strip():
        output = default_output([str(s) for s in sources])
    output_path = Path(output).expanduser()
    for source in sources:
        if source.is_dir() and (output_path == source
                                or str(output_path).startswith(str(source) + os.sep)):
            return {"ok": False,
                    "error": "The output folder cannot sit inside the folder "
                             "being converted. Pick a different destination."}
    with RUN.lock:
        if RUN.process is not None and RUN.process.poll() is None:
            return {"ok": False, "error": "A conversion is already running."}

    # Options first and sources after "--", so a file whose name starts with
    # a dash is a file and never a flag.
    command = [sys.executable, "-u", str(CONVERTER), "-o", str(output_path)]
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
    command += ["--", *[str(source) for source in sources]]

    RUN.reset(str(output_path))
    thread = threading.Thread(target=_run, args=(command,), daemon=True)
    thread.start()
    return {"ok": True, "output": str(output_path)}


def _run(command: list[str]) -> None:
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True,
                                   encoding="utf-8", errors="replace",
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


def default_output(sources: list[str]) -> str:
    """Where the Markdown goes when the person has not said.

    A folder becomes a sibling named after it: `Reports` -> `Reports-markdown`.
    A file, or several, becomes that same sibling of the folder they are in,
    so converting one file today and the whole folder next month lands in the
    same tree, with the file already done. The only exceptions are folders
    that have no sensible sibling — the home folder, or a root — which get a
    `Markdown` folder inside them instead.
    """
    items = [Path(raw).expanduser() for raw in sources if raw]
    if not items:
        return ""
    if len(items) == 1 and items[0].is_dir():
        base = items[0]
    else:
        base = Path(os.path.commonpath(
            [str(item if item.is_dir() else item.parent) for item in items]))
    home = Path.home()
    if base in (home, home.parent) or base.parent == base:
        return str(base / "Markdown")
    return str(base.parent / f"{base.name}-markdown")


def describe_sources(paths: list[str]) -> dict:
    """What the page shows once something has been chosen.

    `path` and `paths` say what; `files` and `kinds` say how much; and
    `suggestedOutput` is where it would go. An empty choice (the dialog was
    cancelled) is an empty answer, not an error.
    """
    paths = [p for p in paths if p]
    result: dict = {"path": paths[0] if paths else "", "paths": paths}
    if paths:
        result.update(count_candidates(paths))
        result["suggestedOutput"] = default_output(paths)
        result["isFolder"] = len(paths) == 1 and Path(paths[0]).is_dir()
    return result


def read_report() -> dict:
    with RUN.lock:
        output = RUN.output_dir
    report = Path(output) / "_conversion-report.md"
    if not report.exists():
        return {"report": ""}
    return {"report": report.read_text(encoding="utf-8")[:200_000]}


def manifest_outputs(output_dir: str) -> list[dict]:
    """The Markdown files the last run produced, from the converter's manifest.

    The page uses this to offer the one file that was just converted rather
    than the folder around it. Anything odd in the file means an empty list,
    never an error: it is a convenience, not the result.
    """
    if not output_dir:
        return []
    try:
        data = json.loads((Path(output_dir) / "_conversion-manifest.json")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    files = data.get("files") if isinstance(data, dict) else None
    outputs = []
    for entry in files if isinstance(files, list) else []:
        if (isinstance(entry, dict) and entry.get("output")
                and entry.get("status") in ("converted", "unchanged")):
            outputs.append({"source": str(entry.get("source", "")),
                            "output": str(entry["output"])})
    return outputs[:500]


def conversion_status(cursor: int) -> dict:
    state = RUN.snapshot(cursor)
    if state["finished"]:
        state["outputs"] = manifest_outputs(state["outputDir"])
    return state


# --------------------------------------------------------------------------
# HTTP plumbing
# --------------------------------------------------------------------------

class Server(ThreadingHTTPServer):
    """A loopback server that does not go looking for itself in DNS.

    HTTPServer.server_bind calls socket.getfqdn() on the address it just bound,
    which is a reverse DNS lookup of 127.0.0.1. On a machine whose resolver is
    slow or unreachable that blocks for half a minute or more, before this
    program has printed a word or written its instance file: from the outside,
    clicking the icon does nothing at all. The name it looks up is only used to
    fill in headers this server does not send.
    """

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        self.server_name = "127.0.0.1"
        self.server_port = self.server_address[1]


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
            self._send(conversion_status(self._cursor(query)))
        elif parsed.path == "/api/report":
            self._send(read_report())
        elif parsed.path == "/api/settings":
            self._send(settings_for_page())
        elif parsed.path == "/api/update-status":
            self._send(UPDATE.snapshot(self._cursor(query)))
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
            if payload.get("kind") == "files":
                chosen = pick_files(payload.get("prompt", "Choose files"))
            else:
                folder = pick_folder(payload.get("prompt", "Choose a folder"))
                chosen = [folder] if folder else []
            self._send(describe_sources(chosen))
        elif parsed.path == "/api/inspect":
            typed = payload.get("path", "")
            expanded = (str(Path(typed).expanduser())
                        if isinstance(typed, str) and typed.strip() else "")
            exists = bool(expanded) and Path(expanded).exists()
            result = describe_sources([expanded] if exists else [])
            result["exists"] = exists
            self._send(result)
        elif parsed.path == "/api/install":
            self._send(start_install())
        elif parsed.path == "/api/update-check":
            self._send(update_status())
        elif parsed.path == "/api/settings":
            self._send(set_auto_check(bool(payload.get("autoCheck"))))
        elif parsed.path == "/api/update":
            self._send(start_update())
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
    server = Server(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    try:
        private_dir(SUPPORT)
        # The token is the key to this server, so the file holding it is
        # created readable by nobody else, before anything is written into it.
        handle = os.open(PORT_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(json.dumps({"port": port, "token": TOKEN}))
    except OSError:
        # Without this file a second launch cannot find this server and will
        # start another one. That is worse than one server, and better than
        # refusing to convert anything at all, so it is not fatal.
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
    # An update installs a newer copy of the app's files in the support
    # folder. On a Mac the bundle's launcher already prefers it; on Windows
    # and Linux the shortcut points at the installed copy, so the preference
    # is made here, and the shipped copy starts the updated one.
    server_script = Path(__file__).resolve()
    updated = PAYLOAD_DIR / "app" / "server.py"
    if (server_script != updated and updated.is_file()
            and (PAYLOAD_DIR / "scripts" / "doc2gfm.py").is_file()):
        server_script = updated
    detach: dict = {"start_new_session": True}
    if IS_WINDOWS:
        # No console of its own, and not tied to the one this was started
        # from, so closing a terminal never takes the app with it.
        detach = {"creationflags": (getattr(subprocess, "DETACHED_PROCESS", 0)
                                    | getattr(subprocess,
                                              "CREATE_NEW_PROCESS_GROUP", 0))}
    with log.open("ab") as handle:
        # cwd is the support folder, not the app's own: on Windows a folder
        # that is some process's working directory cannot be renamed, and an
        # update renames the folder the running app came from.
        subprocess.Popen([sys.executable, str(server_script), "--serve"],
                         stdout=handle, stderr=handle, stdin=subprocess.DEVNULL,
                         cwd=str(SUPPORT), **detach)

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
    """Tell the person something when there may be no terminal to tell."""
    if IS_MAC:
        osascript('on run argv\n'
                  '  display dialog (item 1 of argv) buttons {"OK"} '
                  'default button 1 with icon caution '
                  'with title "Document to Markdown"\n'
                  'end run', message)
        return
    if IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
                None, message, "Document to Markdown", 0x30)
            return
        except (AttributeError, OSError):
            pass
    elif shutil.which("zenity"):
        subprocess.run(["zenity", "--error", "--title=Document to Markdown",
                        f"--text={message}"], capture_output=True)
        return
    print(message, file=sys.stderr)


def selftest() -> int:
    """Start, answer one request, and stop. Prints nothing when it works.

    This is what a downloaded update is put through before it is allowed to
    replace the copy that is already working, so it has to exercise the parts
    that would actually fail: binding the port, serving the page, and finding
    the converter it drives.
    """
    if not CONVERTER.exists():
        print(f"converter missing at {CONVERTER}", file=sys.stderr)
        return 1
    try:
        server = Server(("127.0.0.1", 0), Handler)
    except OSError as exc:
        print(f"could not bind: {exc}", file=sys.stderr)
        return 1
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for path in (f"/api/ping?token={TOKEN}", "/"):
            request = urllib.request.Request(  # noqa: S310 - our own loopback
                f"http://127.0.0.1:{port}{path}")
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
                if response.status != 200:
                    print(f"{path} answered {response.status}", file=sys.stderr)
                    return 1
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"did not answer: {exc}", file=sys.stderr)
        return 1
    finally:
        server.shutdown()
        server.server_close()
    return 0


def main() -> int:
    if "--selftest" in sys.argv:        # used before installing an update
        return selftest()
    if "--serve" in sys.argv:
        return serve()
    if "--no-browser" in sys.argv:      # tests and terminal use
        return serve()
    return launch()


if __name__ == "__main__":
    sys.exit(main())
