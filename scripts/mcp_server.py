#!/usr/bin/env python3
"""MCP server: lets an AI assistant read documents as Markdown, on this machine.

Speaks the Model Context Protocol over standard input and output, one JSON-RPC
message per line, which is the transport Claude Desktop, Claude Code, Cursor,
Gemini CLI, Codex CLI and ChatGPT's desktop app all understand. Standard
library only, like everything else here: nothing to install.

    python3 scripts/mcp_server.py

It offers two tools. `convert_document` turns one file into Markdown text and
hands it back, which is how an assistant should read a PDF or a Word file: as
text, at a fraction of the tokens the raw file costs, without the file leaving
the computer. `convert_folder` runs the batch converter over a folder and
returns the report. docs/ai-agents.md has the two-line configuration for each
assistant.

The converter beside this file does the work, so every format it reads, this
serves. Engines the Document to Markdown app installed for itself are found
automatically.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import doc2gfm  # noqa: E402 - beside this file, found once sys.path says so

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "document-to-markdown", "version": doc2gfm.VERSION}
INSTRUCTIONS = (
    "Documents on this computer - PDF, Word, PowerPoint, Excel, EPUB, HTML, "
    "LaTeX, notebooks and about sixty other formats - should be read through "
    "convert_document rather than opened directly. It returns the text as "
    "Markdown, which costs a fraction of the tokens the raw file would, keeps "
    "headings, lists and tables, and never sends the file anywhere. Use "
    "convert_folder to turn a whole folder into Markdown files on disk."
)
# What one call returns before it stops and says how to ask for the rest. A
# whole book in one tool result helps nobody; a chapter at a time does.
DEFAULT_MAX_CHARS = 120_000
MAX_MAX_CHARS = 2_000_000

TOOLS = [
    {
        "name": "convert_document",
        "title": "Read a document as Markdown",
        "description": (
            "Convert one document on this computer to Markdown and return the "
            "text. Use this before reading any PDF, Word, PowerPoint, Excel, "
            "OpenDocument, RTF, EPUB, HTML, LaTeX, wiki, notebook or similar "
            "file: it is far cheaper in tokens than the raw file and the file "
            "stays on the machine. Long documents come back in pieces: the "
            "result says when there is more and what offset to ask for next."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "Absolute path of the file to read."},
                "ocr": {"type": "boolean", "default": False,
                        "description": "Run OCR on a scanned PDF that has no "
                                       "text layer. Slow; needs ocrmypdf."},
                "offset": {"type": "integer", "default": 0, "minimum": 0,
                           "description": "Character offset to start from, "
                                          "for continuing a long document."},
                "max_chars": {"type": "integer", "default": DEFAULT_MAX_CHARS,
                              "minimum": 1000, "maximum": MAX_MAX_CHARS,
                              "description": "Most characters to return in "
                                             "one call."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "convert_folder",
        "title": "Convert a folder of documents to Markdown files",
        "description": (
            "Convert every document in a folder, including subfolders, into "
            ".md files in an output folder, mirroring the layout, and return "
            "the conversion report. Files already converted and unchanged are "
            "skipped, so re-running is cheap. Nothing inside the source folder "
            "is ever written to."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string",
                           "description": "Absolute path of the folder (or "
                                          "one file) to convert."},
                "output": {"type": "string",
                           "description": "Where the Markdown goes. Defaults "
                                          "to a folder named after the source "
                                          "with -markdown added, beside it."},
                "include": {"type": "array", "items": {"type": "string"},
                            "description": "Only these extensions, e.g. "
                                           "[\"pdf\", \"docx\"]."},
                "force": {"type": "boolean", "default": False,
                          "description": "Reconvert files already done."},
                "flat": {"type": "boolean", "default": False,
                         "description": "Put every file in the output root "
                                        "instead of mirroring the tree."},
                "ocr": {"type": "boolean", "default": False},
            },
            "required": ["source"],
        },
    },
]


def log(message: str) -> None:
    print(f"document-to-markdown: {message}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# The tools
# --------------------------------------------------------------------------

def text_result(text: str, error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": error}


def _int_arg(arguments: dict, name: str, default: int, low: int, high: int) -> int:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(low, min(high, int(value)))


def convert_document(arguments: dict) -> dict:
    path = arguments.get("path")
    if not isinstance(path, str) or not path.strip():
        return text_result("path is required: the absolute path of a file.", True)
    offset = _int_arg(arguments, "offset", 0, 0, 10**12)
    limit = _int_arg(arguments, "max_chars", DEFAULT_MAX_CHARS, 1000, MAX_MAX_CHARS)
    try:
        markdown = doc2gfm.convert_file_to_markdown(
            Path(path), ocr=bool(arguments.get("ocr", False)))
    except doc2gfm.ConversionError as exc:
        return text_result(f"Could not convert {path}: {exc}", True)
    except Exception as exc:  # noqa: BLE001 - the assistant needs the reason, not a crash
        return text_result(f"Could not convert {path}: {type(exc).__name__}: {exc}",
                           True)
    piece = markdown[offset:offset + limit]
    if not piece and offset:
        return text_result(f"Nothing at offset {offset}: the document is "
                           f"{len(markdown)} characters long.", True)
    remaining = len(markdown) - (offset + len(piece))
    if remaining > 0:
        piece += (f"\n\n[... {remaining} more characters. Call convert_document "
                  f"again with offset={offset + len(piece)} to continue.]")
    return text_result(piece)


def convert_folder(arguments: dict) -> dict:
    source = arguments.get("source")
    if not isinstance(source, str) or not source.strip():
        return text_result("source is required: the absolute path of a folder.",
                           True)
    source_path = Path(source).expanduser()
    if not source_path.exists():
        return text_result(f"No such file or folder: {source_path}", True)
    output = arguments.get("output")
    if not isinstance(output, str) or not output.strip():
        base = source_path if source_path.is_dir() else source_path.parent
        output = str(base.parent / f"{base.name}-markdown")
    command = [sys.executable, str(HERE / "doc2gfm.py"), "-o", output, "-q"]
    for extension in arguments.get("include") or []:
        if isinstance(extension, str) and extension.strip():
            command += ["--include", extension.strip().lstrip(".")]
    for flag in ("force", "flat", "ocr"):
        if arguments.get(flag):
            command.append(f"--{flag}")
    command += ["--", str(source_path)]
    try:
        run = subprocess.run(command, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=6 * 3600)
    except subprocess.TimeoutExpired:
        return text_result("The conversion took more than six hours and was "
                           "stopped.", True)
    report_path = Path(output) / "_conversion-report.md"
    try:
        report = report_path.read_text(encoding="utf-8")
    except OSError:
        report = ""
    detail = (run.stderr or run.stdout).strip()
    if run.returncode == 2 or not report:
        return text_result(detail or f"The converter exited with {run.returncode}.",
                           True)
    head = f"Output folder: {output}\n\n"
    if detail:
        head += f"Converter said: {detail}\n\n"
    return text_result(head + report, run.returncode not in (0, 1))


HANDLERS = {"convert_document": convert_document, "convert_folder": convert_folder}


# --------------------------------------------------------------------------
# JSON-RPC over stdio
# --------------------------------------------------------------------------

def error(code: int, message: str) -> dict:
    return {"code": code, "message": message}


def handle(request: dict) -> dict | None:
    """Answer one message. None means it was a notification: nothing to send."""
    method = request.get("method")
    params = request.get("params") or {}
    if not isinstance(params, dict):
        params = {}
    request_id = request.get("id")
    is_notification = "id" not in request

    if method == "initialize":
        asked = str(params.get("protocolVersion") or "")
        version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        result = {"protocolVersion": version,
                  "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": SERVER_INFO,
                  "instructions": INSTRUCTIONS}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        name = params.get("name")
        handler = HANDLERS.get(name) if isinstance(name, str) else None
        if handler is None:
            return None if is_notification else {
                "jsonrpc": "2.0", "id": request_id,
                "error": error(-32602, f"Unknown tool: {name}")}
        arguments = params.get("arguments") or {}
        result = handler(arguments if isinstance(arguments, dict) else {})
    elif method in ("resources/list", "resources/templates/list"):
        result = {"resources": []} if method == "resources/list" else {
            "resourceTemplates": []}
    elif method == "prompts/list":
        result = {"prompts": []}
    elif isinstance(method, str) and method.startswith("notifications/"):
        return None
    else:
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": request_id,
                "error": error(-32601, f"Method not found: {method}")}
    if is_notification:
        return None
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve() -> int:
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer

    def send(message: dict) -> None:
        stdout.write(json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n")
        stdout.flush()

    for raw in stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            send({"jsonrpc": "2.0", "id": None,
                  "error": error(-32700, "Parse error")})
            continue
        messages = message if isinstance(message, list) else [message]
        for item in messages:
            if not isinstance(item, dict):
                send({"jsonrpc": "2.0", "id": None,
                      "error": error(-32600, "Invalid request")})
                continue
            try:
                reply = handle(item)
            except Exception as exc:  # noqa: BLE001 - one bad request must not end the session
                log(f"{item.get('method')}: {type(exc).__name__}: {exc}")
                reply = None if "id" not in item else {
                    "jsonrpc": "2.0", "id": item.get("id"),
                    "error": error(-32603, f"{type(exc).__name__}: {exc}")}
            if reply is not None:
                send(reply)
    return 0


if __name__ == "__main__":
    sys.exit(serve())
