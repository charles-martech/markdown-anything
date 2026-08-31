"""Tests for the parts that have to keep being true.

Standard library only, so `python3 -m unittest discover tests` works on a
fresh machine with nothing installed. Most of these guard the local server's
front door: it holds a token that can start processes and read folders, and
every check that keeps it shut is pinned here.
"""

from __future__ import annotations

import http.client
import importlib.util
import os
import sys
import tarfile
import tempfile
import threading
import unittest
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Point the app's support folder at a throwaway directory before importing it,
# so a test run never reads or writes the real one.
_HOME = tempfile.mkdtemp(prefix="doc2gfm-tests-")
os.environ["DOC2MD_HOME"] = _HOME


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # @dataclass looks the module up by name while the class is being built.
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


server = _load("mda_server", ROOT / "app" / "server.py")
doc2gfm = _load("mda_doc2gfm", ROOT / "scripts" / "doc2gfm.py")


class ServerDoorTest(unittest.TestCase):
    """The token, and the host checks that stop another site stealing it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def request(self, path: str, method: str = "GET", headers: dict | None = None,
                body: bytes | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read().decode("utf-8", "replace")
        finally:
            connection.close()

    def test_page_carries_the_token(self) -> None:
        status, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(server.TOKEN, body)
        self.assertNotIn("__TOKEN__", body)

    def test_page_refuses_another_sites_name(self) -> None:
        # A domain pointed at 127.0.0.1 would otherwise be same-origin with the
        # app and could read the token straight out of the page.
        status, _ = self.request("/", headers={"Host": "evil.example.com"})
        self.assertEqual(status, 403)

    def test_api_refuses_a_cross_site_origin(self) -> None:
        status, _ = self.request(
            f"/api/status?token={server.TOKEN}",
            headers={"Origin": "https://evil.example.com"})
        self.assertEqual(status, 403)

    def test_api_refuses_a_cross_site_fetch(self) -> None:
        status, _ = self.request(f"/api/status?token={server.TOKEN}",
                                 headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)

    def test_api_needs_the_token(self) -> None:
        self.assertEqual(self.request("/api/status")[0], 403)
        self.assertEqual(self.request("/api/status?token=wrong")[0], 403)
        self.assertEqual(self.request("/api/engines?token=wrong")[0], 403)

    def test_api_answers_with_the_token(self) -> None:
        status, body = self.request(f"/api/ping?token={server.TOKEN}")
        self.assertEqual(status, 200)
        self.assertIn('"ok"', body)

    def test_nonsense_cursor_is_not_an_error(self) -> None:
        status, _ = self.request(f"/api/status?token={server.TOKEN}&cursor=abc")
        self.assertEqual(status, 200)

    def test_oversized_body_is_refused(self) -> None:
        status, _ = self.request(
            f"/api/convert?token={server.TOKEN}", method="POST",
            headers={"Content-Length": str(server.MAX_BODY + 1),
                     "Content-Type": "application/json"},
            body=b"{}" + b" " * server.MAX_BODY)
        self.assertEqual(status, 413)

    def test_unknown_path_is_not_found(self) -> None:
        status, _ = self.request(f"/api/nope?token={server.TOKEN}")
        self.assertEqual(status, 404)


class DownloadTest(unittest.TestCase):
    def test_only_github_over_https(self) -> None:
        self.assertTrue(server.trusted_download(
            "https://github.com/jgm/pandoc/releases/download/3.1.11/x.zip"))
        self.assertTrue(server.trusted_download(
            "https://objects.githubusercontent.com/x"))
        self.assertFalse(server.trusted_download("http://github.com/x.zip"))
        self.assertFalse(server.trusted_download("https://evil.example.com/x.zip"))
        self.assertFalse(server.trusted_download("file:///etc/passwd"))

    def test_zip_member_cannot_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../escaped.txt", "no")
            with self.assertRaises(server.UnsafeArchive):
                server.extract_archive(archive, "bad.zip", Path(tmp) / "out")
            self.assertFalse((Path(tmp) / "escaped.txt").exists())

    def test_tar_member_cannot_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = Path(tmp) / "payload"
            payload.write_text("no")
            archive = Path(tmp) / "bad.tar.gz"
            with tarfile.open(archive, "w:gz") as tf:
                tf.add(payload, arcname="../escaped.txt")
            with self.assertRaises(server.UnsafeArchive):
                server.extract_archive(archive, "bad.tar.gz", Path(tmp) / "out")
            self.assertFalse((Path(tmp) / "escaped.txt").exists())

    def test_ordinary_archive_still_unpacks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "good.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("pandoc-3/bin/pandoc", "binary")
            out = Path(tmp) / "out"
            server.extract_archive(archive, "good.zip", out)
            self.assertTrue((out / "pandoc-3" / "bin" / "pandoc").is_file())


class DialogTest(unittest.TestCase):
    def test_prompt_cannot_carry_applescript(self) -> None:
        cleaned = server.PROMPT_RE.sub(
            " ", 'x" & (do shell script "rm -rf ~") & "')
        self.assertNotIn('"', cleaned)
        self.assertNotIn("&", cleaned)

    def test_reveal_refuses_a_path_that_is_not_there(self) -> None:
        self.assertFalse(server.reveal("/no/such/path/anywhere"))
        self.assertFalse(server.reveal(""))
        self.assertFalse(server.reveal(None))


class ConverterTest(unittest.TestCase):
    def test_front_matter_survives_a_quote_in_the_name(self) -> None:
        base = Path(tempfile.gettempdir()) / "quoted"
        job = doc2gfm.Job(source=base / 'od"d\\name.docx', dest=base / "o.md",
                          route="pandoc", arg="docx", sha256="abc")
        args = type("Args", (), {"front_matter": True,
                                 "front_matter_base": str(base)})()
        block = doc2gfm.front_matter(job, args)
        for line in block.splitlines():
            if line.startswith("source:"):
                self.assertTrue(line.endswith('"'))
                self.assertEqual(line.count('"') - line.count('\\"'), 2)

    def test_xml_declaring_entities_is_refused(self) -> None:
        bomb = (b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "boom">]><a>&x;</a>')
        with self.assertRaises(doc2gfm.ConversionError):
            doc2gfm.parse_xml(bomb)

    def test_ordinary_xml_still_parses(self) -> None:
        self.assertEqual(doc2gfm.parse_xml(b"<a><b/></a>").tag, "a")

    def test_routing_knows_the_common_formats(self) -> None:
        self.assertEqual(doc2gfm.route_for(Path("a.docx")), ("pandoc", "docx"))
        self.assertEqual(doc2gfm.route_for(Path("a.pdf")), ("pdf", ""))
        self.assertEqual(doc2gfm.route_for(Path("a.pptx")), ("pptx", ""))
        self.assertIsNone(doc2gfm.route_for(Path("a.png")))

    def test_table_cells_cannot_break_out_of_the_table(self) -> None:
        rows = doc2gfm._gfm_table([["a|b", "c"], ["d", "e"]])
        self.assertIn("a\\|b", rows[0])


if __name__ == "__main__":
    unittest.main()
