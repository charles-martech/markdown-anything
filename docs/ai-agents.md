# Using it from Claude, ChatGPT, Gemini, Cursor and other AI tools

An AI assistant that reads a PDF or a Word file directly pays for every page
twice over: the file is either rendered as images or read as a zip of XML,
both of which cost many more tokens than the words in it, fill the context
window in a few pages, and lose the headings and tables that make a document
usable. Converting to Markdown first, on the machine, fixes all three: the
assistant reads plain text with the structure kept, at a fraction of the
tokens, and the document never leaves the computer.

This repository offers that in three shapes. Pick whichever fits the tool.

| Tool | Use |
| --- | --- |
| Claude Code | the skill in `.claude/skills/doc-to-gfm/` |
| Claude Desktop, Claude Code, Cursor, Gemini CLI, Codex CLI, ChatGPT desktop, anything that speaks MCP | the MCP server, `scripts/mcp_server.py` |
| Any assistant that can run a shell command | `python3 scripts/doc2gfm.py FILE --stdout` |

All three run the same converter, and all three find the engines the
Document to Markdown app installed, so anyone who has set the app up once has
a working converter everywhere.

## The one-line command

```bash
python3 scripts/doc2gfm.py ~/Downloads/contract.pdf --stdout
```

Prints the Markdown and writes nothing. That is the whole interface for an
assistant that can run commands: convert, then read. `-o FOLDER` instead of
`--stdout` writes one `.md` per file plus a report, for a folder or for
anything worth keeping.

## The Claude Code skill

Clone this repository, or copy `.claude/skills/doc-to-gfm/` into
`~/.claude/skills/` to have it in every project. Claude Code reads the skill's
description and applies it whenever a task involves the contents of a
document: it converts the file with the command above and reads the Markdown,
rather than opening the PDF.

## The MCP server

`scripts/mcp_server.py` speaks the [Model Context Protocol](https://modelcontextprotocol.io)
over standard input and output. It is standard library only, so pointing a
client at it is the whole setup. It offers two tools:

- **`convert_document`** — one file in, Markdown text back. Long documents
  come in pieces: the result says how much is left and what offset to ask for
  next, so a 400-page manual does not land in the context window at once.
- **`convert_folder`** — a whole folder to `.md` files on disk, mirrored,
  with the report returned.

The server also tells the client, in the instructions it sends when it
connects, that documents should be read through it rather than opened
directly. Clients pass that on to the model.

Below, `PATH` is the absolute path of `scripts/mcp_server.py` in your clone,
or, if you installed the app, of the copy inside it:

| Where | `PATH` |
| --- | --- |
| A clone of this repository | `/wherever/markdown-anything/scripts/mcp_server.py` |
| The Mac app | `/Applications/Document to Markdown.app/Contents/Resources/scripts/mcp_server.py` |
| The Windows app | `%LOCALAPPDATA%\Document to Markdown\bundle\scripts\mcp_server.py` |
| The Linux app | `~/.local/share/document-to-markdown/bundle/scripts/mcp_server.py` |

Use `python3` on a Mac or Linux and `python` on Windows.

### Claude Code

```bash
claude mcp add document-to-markdown -- python3 PATH
```

### Claude Desktop

In `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "document-to-markdown": {
      "command": "python3",
      "args": ["PATH"]
    }
  }
}
```

### Cursor

In `.cursor/mcp.json` in the project, or `~/.cursor/mcp.json` for everywhere:

```json
{
  "mcpServers": {
    "document-to-markdown": {
      "command": "python3",
      "args": ["PATH"]
    }
  }
}
```

### Gemini CLI

In `~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "document-to-markdown": {
      "command": "python3",
      "args": ["PATH"]
    }
  }
}
```

### Codex CLI

In `~/.codex/config.toml`:

```toml
[mcp_servers.document_to_markdown]
command = "python3"
args = ["PATH"]
```

### ChatGPT and everything else

Any client that can run a local MCP server over stdio takes the same three
values: a name, the command `python3`, and the argument `PATH`. Where a client
only accepts remote servers, or offers no MCP at all, the one-line command
above still works from any assistant that can run a shell command.

### Trying it by hand

The server reads one JSON-RPC message per line. This asks it for its tools:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"me","version":"0"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | python3 scripts/mcp_server.py
```

## Telling an assistant to prefer it

Tools that read a project instructions file — `AGENTS.md`, `GEMINI.md`,
`.cursorrules`, `CLAUDE.md` — can be given the rule directly. This paragraph
is enough:

> Before reading any PDF, Word, PowerPoint, Excel, EPUB or other document
> file, convert it to Markdown with `python3 PATH_TO/doc2gfm.py FILE --stdout`
> (or the `convert_document` MCP tool) and read that instead. It is far
> cheaper in tokens, keeps the document's structure, and runs locally.

## What leaves the machine

Nothing. The converter never opens a network connection, and the MCP server
talks to the assistant's client through a pipe on this computer. What the
assistant then does with the text it was given is between you and the
assistant, as it is with anything you paste into a chat.
