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
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import threading
import unittest
import zipfile
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
        cls.server = server.Server(("127.0.0.1", 0), server.Handler)
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
        self.assertEqual(self.request("/api/version?token=wrong")[0], 403)
        self.assertEqual(
            self.request("/api/update", method="POST")[0], 403)

    def test_api_answers_with_the_token(self) -> None:
        status, body = self.request(f"/api/ping?token={server.TOKEN}")
        self.assertEqual(status, 200)
        self.assertIn('"ok"', body)

    def test_nonsense_cursor_is_not_an_error(self) -> None:
        status, _ = self.request(f"/api/status?token={server.TOKEN}&cursor=abc")
        self.assertEqual(status, 200)

    def test_oversized_body_is_refused(self) -> None:
        # Only the declared length is sent, not a real megabyte: the server
        # refuses on the header alone, and a client still writing the body
        # into the socket it just closed would fail for the wrong reason.
        status, _ = self.request(
            f"/api/convert?token={server.TOKEN}", method="POST",
            headers={"Content-Length": str(server.MAX_BODY + 1),
                     "Content-Type": "application/json"},
            body=b"{}")
        self.assertEqual(status, 413)

    def test_unknown_path_is_not_found(self) -> None:
        status, _ = self.request(f"/api/nope?token={server.TOKEN}")
        self.assertEqual(status, 404)


class BindTest(unittest.TestCase):
    def test_binding_does_not_wait_on_dns(self) -> None:
        """The stock HTTPServer reverse-resolves 127.0.0.1 while binding.

        On a machine whose resolver is slow or unreachable that stalls the
        whole app before it prints anything, which looks exactly like an icon
        that does nothing. Nothing here may go near a name server.
        """
        import socket
        asked = []
        original = socket.getfqdn
        socket.getfqdn = lambda *args: asked.append(args) or "should-not-happen"
        try:
            bound = server.Server(("127.0.0.1", 0), server.Handler)
            bound.server_close()
        finally:
            socket.getfqdn = original
        self.assertEqual(asked, [])
        self.assertEqual(bound.server_name, "127.0.0.1")


class UpdateTest(unittest.TestCase):
    """The update path. Nothing here is allowed to reach the network."""

    def payload(self, directory: Path, version: str) -> Path:
        """A tree shaped like a release archive of this app."""
        root = directory / f"markdown-anything-{version}"
        (root / "app").mkdir(parents=True)
        (root / "scripts").mkdir(parents=True)
        (root / "app" / "server.py").write_text("")
        (root / "app" / "index.html").write_text("")
        (root / "scripts" / "doc2gfm.py").write_text("")
        (root / "VERSION").write_text(version + "\n")
        (root / "BUNDLE_FORMAT").write_text("1\n")
        return root

    def test_versions_compare_by_number_not_text(self) -> None:
        self.assertGreater(server.version_tuple("1.0.10"),
                           server.version_tuple("1.0.9"))
        self.assertGreater(server.version_tuple("v1.1.0"),
                           server.version_tuple("1.0.99"))
        self.assertEqual(server.version_tuple("v1.0.3"),
                         server.version_tuple("1.0.3"))

    def test_only_a_version_number_is_accepted_as_a_tag(self) -> None:
        # The tag arrives from the network and is put into a download URL.
        for good in ("v1.0.3", "1.0.3", "v2", "v1.2.3.4"):
            self.assertTrue(server.TAG_RE.fullmatch(good), good)
        for bad in ("v1.0.3; rm -rf ~", "../../etc/passwd", "latest",
                    "v1.0.3/../../x", "v1.0.3\n", ""):
            self.assertFalse(server.TAG_RE.fullmatch(bad), bad)

    def test_release_archive_is_recognised_through_its_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            unpacked = Path(tmp)
            root = self.payload(unpacked, "1.0.5")
            self.assertEqual(server.payload_root(unpacked), root)

    def test_an_archive_missing_the_app_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            unpacked = Path(tmp)
            root = self.payload(unpacked, "1.0.5")
            (root / "scripts" / "doc2gfm.py").unlink()
            with self.assertRaises(server.UnsafeArchive):
                server.payload_root(unpacked)

    def test_a_release_older_than_the_version_stamp_is_refused(self) -> None:
        # Releases before the update mechanism existed carry no VERSION file.
        with tempfile.TemporaryDirectory() as tmp:
            unpacked = Path(tmp)
            root = self.payload(unpacked, "1.0.5")
            (root / "VERSION").unlink()
            with self.assertRaises(server.UnsafeArchive):
                server.payload_root(unpacked)

    def test_installing_replaces_what_was_there_and_leaves_no_debris(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = self.payload(Path(tmp) / "a", "1.0.5")
            second = self.payload(Path(tmp) / "b", "1.0.6")
            server.install_payload(first)
            self.assertEqual(
                (server.PAYLOAD_DIR / "VERSION").read_text().strip(), "1.0.5")
            server.install_payload(second)
            self.assertEqual(
                (server.PAYLOAD_DIR / "VERSION").read_text().strip(), "1.0.6")
            leftovers = {p.name for p in server.SUPPORT.iterdir()}
            self.assertNotIn("current.new", leftovers)
            self.assertNotIn("current.old", leftovers)
            shutil.rmtree(server.PAYLOAD_DIR)

    def test_the_app_knows_its_own_version(self) -> None:
        # Read from the VERSION file the build stamps beside the app's files.
        self.assertNotEqual(server.VERSION, "0.0.0")
        self.assertTrue(server.TAG_RE.fullmatch(server.VERSION), server.VERSION)


class UpdatePreferenceTest(unittest.TestCase):
    """Whether the app looks for updates on its own is the person's answer."""

    def setUp(self) -> None:
        server.SETTINGS_FILE.unlink(missing_ok=True)

    tearDown = setUp

    def test_unanswered_until_the_person_answers(self) -> None:
        # None, not False: the page needs to know it has never been asked, so
        # that it asks instead of quietly deciding.
        self.assertIsNone(server.settings_for_page()["autoCheck"])
        self.assertFalse(server.settings_for_page()["checkNow"])

    def test_the_answer_is_remembered_both_ways(self) -> None:
        for answer in (True, False, True):
            server.set_auto_check(answer)
            self.assertEqual(server.settings_for_page()["autoCheck"], answer)

    def test_saying_no_means_nothing_is_ever_due(self) -> None:
        server.set_auto_check(False)
        self.assertFalse(server.settings_for_page()["checkNow"])

    def test_a_check_is_due_once_a_day_and_not_more(self) -> None:
        server.set_auto_check(True)
        self.assertTrue(server.settings_for_page()["checkNow"])
        server.note_check()
        self.assertFalse(server.settings_for_page()["checkNow"])
        saved = server.read_settings()
        saved["lastCheck"] = time.time() - server.CHECK_INTERVAL - 1
        server.write_settings(saved)
        self.assertTrue(server.settings_for_page()["checkNow"])

    def test_the_answer_is_not_readable_by_other_accounts(self) -> None:
        server.set_auto_check(True)
        self.assertEqual(server.SETTINGS_FILE.stat().st_mode & 0o777, 0o600)

    def test_a_damaged_settings_file_is_not_an_answer(self) -> None:
        server.SETTINGS_FILE.write_text("{not json")
        self.assertIsNone(server.settings_for_page()["autoCheck"])


class SelftestTest(unittest.TestCase):
    def test_a_working_copy_passes(self) -> None:
        """--selftest is what an update is put through before it is trusted."""
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, str(ROOT / "app" / "server.py"), "--selftest"],
                capture_output=True, text=True, timeout=120,
                env={**os.environ, "DOC2MD_HOME": tmp})
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_copy_without_its_converter_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "broken"
            (broken / "app").mkdir(parents=True)
            shutil.copy(ROOT / "app" / "server.py", broken / "app" / "server.py")
            result = subprocess.run(
                [sys.executable, str(broken / "app" / "server.py"), "--selftest"],
                capture_output=True, text=True, timeout=120,
                env={**os.environ, "DOC2MD_HOME": tmp})
            self.assertEqual(result.returncode, 1)
            self.assertIn("converter missing", result.stderr)


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

    def test_links_in_an_archive_are_not_written(self) -> None:
        # Pandoc's own tarball ships pandoc-lua and pandoc-server as symlinks.
        # They are skipped rather than refused, so the real pandoc beside them
        # still arrives, and no link is ever created to redirect a later write.
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "pandoc"
            real.write_text("binary")
            link = Path(tmp) / "pandoc-lua"
            link.symlink_to("pandoc")
            archive = Path(tmp) / "linked.tar.gz"
            with tarfile.open(archive, "w:gz") as tf:
                tf.add(real, arcname="pandoc-3/bin/pandoc")
                tf.add(link, arcname="pandoc-3/bin/pandoc-lua")
            out = Path(tmp) / "out"
            server.extract_archive(archive, "linked.tar.gz", out)
            self.assertTrue((out / "pandoc-3" / "bin" / "pandoc").is_file())
            self.assertFalse((out / "pandoc-3" / "bin" / "pandoc-lua").exists())

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
