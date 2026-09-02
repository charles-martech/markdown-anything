"""Tests for the parts that have to keep being true.

Standard library only, so `python3 -m unittest discover tests` works on a
fresh machine with nothing installed. Most of these guard the local server's
front door: it holds a token that can start processes and read folders, and
every check that keeps it shut is pinned here.
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import threading
import unittest
import unittest.mock
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


# The diagram tests need pymupdf; CI does not install it, so they skip
# there. Bound here rather than inside each test so it is always a
# name, never a variable that may or may not have been assigned.
try:
    import pymupdf  # type: ignore
except ImportError:
    pymupdf = None

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

    @unittest.skipIf(sys.platform == "win32", "Windows has no Unix file modes")
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
            try:
                link.symlink_to("pandoc")
            except OSError as exc:   # Windows without developer mode
                self.skipTest(f"cannot create a symlink here: {exc}")
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

    @staticmethod
    def _pdf_args(**over):
        base = {"pdf_page_marks": False}
        base.update(over)
        return type("Args", (), base)()

    def test_a_diagram_picture_replaces_the_text_scraped_out_of_it(self) -> None:
        """The scraped text is drawing-order gibberish; the picture is not."""
        page = ("Before\n\n<!-- Start of picture text -->\nsey / otro box\n"
                "<!-- End of picture text -->\n\nAfter")
        out = doc2gfm.assemble_pdf([page], {1: "page-001.png"}, "d.media/",
                                   self._pdf_args())
        self.assertIn("![Diagram on page 1 of the original PDF]"
                      "(d.media/page-001.png)", out)
        self.assertNotIn("sey / otro box", out)
        self.assertIn("Before", out)
        self.assertIn("After", out)

    def test_a_diagram_picture_leads_a_page_it_could_not_be_placed_in(self) -> None:
        """A page's last line is usually the next section's heading."""
        out = doc2gfm.assemble_pdf(["Just words"], {1: "page-001.png"},
                                   "d.media/", self._pdf_args())
        self.assertLess(out.index("page-001.png"), out.index("Just words"))

    def test_a_replaced_picture_stays_where_the_scraped_text_was(self) -> None:
        page = ("before\n\n<!-- Start of picture text -->x"
                "<!-- End of picture text -->\n\nafter")
        out = doc2gfm.assemble_pdf([page], {1: "page-001.png"}, "d.media/",
                                   self._pdf_args())
        self.assertLess(out.index("before"), out.index("page-001.png"))
        self.assertLess(out.index("page-001.png"), out.index("after"))

    def test_pages_without_a_diagram_are_left_alone(self) -> None:
        out = doc2gfm.assemble_pdf(["one", "", "two"], {}, "", self._pdf_args())
        self.assertEqual(out, "one\n\ntwo\n")

    def test_page_marks_stay_optional(self) -> None:
        self.assertNotIn("<!-- page", doc2gfm.assemble_pdf(
            ["one"], {}, "", self._pdf_args()))
        self.assertIn("<!-- page 1 -->", doc2gfm.assemble_pdf(
            ["one"], {}, "", self._pdf_args(pdf_page_marks=True)))

    def test_a_pdf_without_vector_art_yields_no_diagram_pages(self) -> None:
        if pymupdf is None:
            self.skipTest("pymupdf is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "plain.pdf"
            doc = pymupdf.open()
            doc.new_page().insert_text((72, 72), "nothing but words")
            doc.save(str(plain))
            self.assertEqual(doc2gfm.diagram_pages(plain), {})
            self.assertEqual(
                doc2gfm.save_diagram_pictures(plain, Path(tmp) / "m", 72), {})
            self.assertFalse((Path(tmp) / "m").exists())

    def test_a_pdf_full_of_boxes_is_read_as_a_diagram(self) -> None:
        if pymupdf is None:
            self.skipTest("pymupdf is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            chart = Path(tmp) / "chart.pdf"
            doc = pymupdf.open()
            page = doc.new_page()
            for row in range(6):
                for column in range(5):
                    page.draw_rect(pymupdf.Rect(40 + column * 100,
                                                40 + row * 120,
                                                120 + column * 100,
                                                110 + row * 120))
            doc.save(str(chart))
            self.assertEqual(list(doc2gfm.diagram_pages(chart)), [1])
            media = Path(tmp) / "m"
            self.assertEqual(doc2gfm.save_diagram_pictures(chart, media, 72),
                             {1: "page-001.png"})
            self.assertTrue((media / "page-001.png").is_file())

    def test_a_missing_pdf_never_raises_from_the_diagram_check(self) -> None:
        self.assertEqual(doc2gfm.diagram_pages(Path("no-such-file.pdf")), {})

    def test_a_page_never_carries_the_same_picture_twice(self) -> None:
        """A page drawn entirely as vector art has no text to stand it beside."""
        for page in ("",
                     "just words",
                     "<!-- Start of picture text -->x<!-- End of picture text -->",
                     "<!-- Start of picture text -->x<!-- End of picture text -->"
                     "\n\nmid\n\n"
                     "<!-- Start of picture text -->y<!-- End of picture text -->"):
            out = doc2gfm.assemble_pdf([page], {1: "page-001.png"}, "d.media/",
                                       self._pdf_args())
            self.assertEqual(out.count("page-001.png"), 1, page[:40])

    def test_a_second_scraped_block_is_kept_rather_than_deleted(self) -> None:
        page = ("<!-- Start of picture text -->first<!-- End of picture text -->"
                "\n\n<!-- Start of picture text -->second<!-- End of picture text -->")
        out = doc2gfm.assemble_pdf([page], {1: "page-001.png"}, "d.media/",
                                   self._pdf_args())
        self.assertNotIn("first", out)
        self.assertIn("second", out)

    def test_the_reader_advice_never_contradicts_the_fallback(self) -> None:
        """After a fall back pymupdf4llm is installed, so do not ask for it."""
        self.assertEqual(doc2gfm.engine_advice("pdftotext", True, True), "")
        self.assertEqual(doc2gfm.engine_advice("pdftotext", True, False), "")

    def test_the_reader_advice_appears_when_it_is_really_missing(self) -> None:
        self.assertIn("better PDF text",
                      doc2gfm.engine_advice("pdftotext", False, True))
        self.assertIn("diagrams saved as pictures",
                      doc2gfm.engine_advice("pdftotext", False, True))

    def test_the_reader_advice_omits_pictures_nobody_asked_for(self) -> None:
        advice = doc2gfm.engine_advice("pdftotext", False, False)
        self.assertIn("better PDF text", advice)
        self.assertNotIn("pictures", advice)

    def test_no_advice_when_pymupdf4llm_read_the_file(self) -> None:
        self.assertEqual(doc2gfm.engine_advice("pymupdf", False, True), "")

    def test_text_volume_counts_words_not_markup(self) -> None:
        self.assertEqual(doc2gfm.text_volume(["## ab |---| cd"]), 4)
        self.assertEqual(doc2gfm.text_volume([]), 0)

    def test_letter_sized_shapes_are_not_a_diagram(self) -> None:
        """Older PyMuPDF returns each glyph's outline from get_drawings."""
        if pymupdf is None:
            self.skipTest("pymupdf is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            prose = Path(tmp) / "prose.pdf"
            doc = pymupdf.open()
            page = doc.new_page()
            for row in range(20):
                for column in range(16):
                    # About the size of a letter, which is what a page of text
                    # looks like through an older reader.
                    page.draw_rect(pymupdf.Rect(40 + column * 30, 40 + row * 30,
                                                45 + column * 30, 47 + row * 30))
            doc.save(str(prose))
            self.assertEqual(doc2gfm.diagram_pages(prose), {})

    def test_a_document_where_every_page_looks_drawn_saves_none(self) -> None:
        """Some PyMuPDF versions count ruled prose pages as many shapes."""
        if pymupdf is None:
            self.skipTest("pymupdf is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            everything = Path(tmp) / "everything.pdf"
            doc = pymupdf.open()
            for _ in range(8):
                page = doc.new_page()
                for row in range(6):
                    for column in range(5):
                        page.draw_rect(pymupdf.Rect(40 + column * 100,
                                                    40 + row * 120,
                                                    120 + column * 100,
                                                    110 + row * 120))
            doc.save(str(everything))
            self.assertEqual(doc2gfm.diagram_pages(everything), {})

    def test_a_few_drawn_pages_among_many_are_still_saved(self) -> None:
        if pymupdf is None:
            self.skipTest("pymupdf is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            mixed = Path(tmp) / "mixed.pdf"
            doc = pymupdf.open()
            for index in range(8):
                page = doc.new_page()
                if index in (2, 5):
                    for row in range(6):
                        for column in range(5):
                            page.draw_rect(pymupdf.Rect(40 + column * 100,
                                                        40 + row * 120,
                                                        120 + column * 100,
                                                        110 + row * 120))
                else:
                    page.insert_text((72, 72), "nothing but words")
            doc.save(str(mixed))
            self.assertEqual(list(doc2gfm.diagram_pages(mixed)), [3, 6])


def _python(major: int, minor: int):
    """A stand-in for sys.version_info; the real type cannot be constructed."""
    import collections
    return collections.namedtuple(
        "V", "major minor micro releaselevel serial")(major, minor, 0, "final", 0)


class ReaderCeilingTest(unittest.TestCase):
    """What to say when the Python cannot take a reader worth upgrading to."""

    def test_a_python_that_can_take_a_newer_reader(self) -> None:
        with unittest.mock.patch.object(doc2gfm.sys, "version_info",
                                        _python(3, 10)):
            self.assertTrue(doc2gfm.newer_reader_installable())
            self.assertIn("A newer pymupdf4llm", doc2gfm.reader_ceiling_note())
            self.assertIn("better PDF text",
                          doc2gfm.engine_advice("pdftotext", False, False))

    def test_a_python_that_cannot(self) -> None:
        with unittest.mock.patch.object(doc2gfm.sys, "version_info",
                                        _python(3, 9)):
            self.assertFalse(doc2gfm.newer_reader_installable())
            note = doc2gfm.reader_ceiling_note()
            self.assertIn("Python 3.9 cannot install", note)
            self.assertIn("needs Python 3.10", note)

    def test_only_pictures_are_promised_on_an_older_python(self) -> None:
        with unittest.mock.patch.object(doc2gfm.sys, "version_info",
                                        _python(3, 9)):
            advice = doc2gfm.engine_advice("pdftotext", False, True)
            self.assertIn("diagrams saved as pictures", advice)
            self.assertNotIn("better PDF text", advice)
            # Nothing at all is gained where pictures were not wanted.
            self.assertEqual(doc2gfm.engine_advice("pdftotext", False, False),
                             "")

    def test_the_app_offers_only_what_the_python_can_deliver(self) -> None:
        def purpose():
            return next(e for e in server.engine_status()
                        if e["key"] == "pdf")["purpose"]
        with unittest.mock.patch.object(server.sys, "version_info",
                                        _python(3, 9)):
            self.assertNotIn("headings and tables", purpose())
            self.assertIn("diagrams", purpose())
        with unittest.mock.patch.object(server.sys, "version_info",
                                        _python(3, 11)):
            self.assertIn("headings and tables", purpose())

    def test_both_files_agree_on_the_ceiling(self) -> None:
        self.assertEqual(doc2gfm.READER_NEEDS_PYTHON,
                         server.READER_NEEDS_PYTHON)


class StaleProcessTest(unittest.TestCase):
    """An update replaces the bundle under a server that is already running."""

    def test_matching_versions_say_nothing(self) -> None:
        self.assertEqual(server.installed_version(), server.VERSION)
        self.assertEqual(server.settings_for_page()["installedVersion"],
                         server.VERSION)

    def test_a_newer_bundle_on_disk_is_reported(self) -> None:
        stamp = server.ROOT / "VERSION"
        original = stamp.read_text(encoding="utf-8")
        try:
            stamp.write_text("99.0.0\n", encoding="utf-8")
            self.assertEqual(server.installed_version(), "99.0.0")
            payload = server.settings_for_page()
            self.assertEqual(payload["installedVersion"], "99.0.0")
            # The running version is the one read at import, not re-read here.
            self.assertEqual(payload["version"], server.VERSION)
            self.assertNotEqual(payload["installedVersion"], payload["version"])
        finally:
            stamp.write_text(original, encoding="utf-8")

    def test_an_unreadable_stamp_reports_the_running_version(self) -> None:
        stamp = server.ROOT / "VERSION"
        original = stamp.read_text(encoding="utf-8")
        try:
            stamp.unlink()
            self.assertEqual(server.installed_version(), server.VERSION)
        finally:
            stamp.write_text(original, encoding="utf-8")


class SidecarTest(unittest.TestCase):
    """The report and manifest are the app's record, not the person's files."""

    def test_the_app_keeps_them_out_of_the_folder_being_converted(self) -> None:
        self.assertTrue(server.RUN_REPORT.is_relative_to(server.SUPPORT))
        self.assertTrue(server.RUN_MANIFEST.is_relative_to(server.SUPPORT))

    def test_the_converter_can_be_told_to_write_neither(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "in"
            source.mkdir()
            (source / "a.txt").write_text("hi\n")
            out = Path(tmp) / "out"
            code = doc2gfm.main(["-q", "-o", str(out), "--no-sidecars",
                                 "--", str(source)])
            self.assertEqual(code, 0)
            self.assertEqual(sorted(p.name for p in out.iterdir()), ["a.md"])

    def test_the_converter_still_writes_them_where_it_is_told(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "in"
            source.mkdir()
            (source / "a.txt").write_text("hi\n")
            report = Path(tmp) / "elsewhere" / "r.md"
            manifest = Path(tmp) / "elsewhere" / "m.json"
            doc2gfm.main(["-q", "-o", str(tmp) + "/out",
                          "--report", str(report), "--manifest", str(manifest),
                          "--", str(source)])
            self.assertTrue(report.is_file())
            self.assertIn("files", json.loads(manifest.read_text()))
            self.assertEqual(
                sorted(p.name for p in (Path(tmp) / "out").iterdir()),
                ["a.md"])


class ChoosingTest(unittest.TestCase):
    """Files and folders as the page sends them, and where the output goes."""

    def test_a_folder_gets_a_sibling_named_after_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "Reports"
            folder.mkdir()
            self.assertEqual(server.default_output([str(folder)]),
                             str(Path(tmp) / "Reports-markdown"))

    def test_a_file_goes_where_its_folder_would(self) -> None:
        # Converting one file today and the folder next month must land in
        # the same tree, with the file already done.
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "Reports"
            folder.mkdir()
            (folder / "a.pdf").write_bytes(b"%PDF")
            (folder / "b.docx").write_bytes(b"PK")
            expected = str(Path(tmp) / "Reports-markdown")
            self.assertEqual(server.default_output([str(folder / "a.pdf")]), expected)
            self.assertEqual(server.default_output(
                [str(folder / "a.pdf"), str(folder / "b.docx")]), expected)

    def test_files_from_two_folders_share_their_common_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("one", "two"):
                (Path(tmp) / name).mkdir()
                (Path(tmp) / name / "x.txt").write_text("x")
            got = server.default_output(
                [str(Path(tmp) / "one" / "x.txt"), str(Path(tmp) / "two" / "x.txt")])
            self.assertEqual(got, str(Path(tmp).parent / f"{Path(tmp).name}-markdown"))

    def test_a_file_in_the_home_folder_does_not_get_a_sibling_of_home(self) -> None:
        # ~/report.pdf must not suggest /Users/me-markdown, which is not
        # somewhere anyone can write.
        home = Path.home()
        got = server.default_output([str(home / "report.pdf")])
        self.assertEqual(got, str(home / "Markdown"))
        self.assertEqual(server.default_output([str(home)]), str(home / "Markdown"))

    def test_nothing_chosen_is_nothing(self) -> None:
        self.assertEqual(server.default_output([]), "")
        self.assertEqual(server.default_output([""]), "")
        self.assertEqual(server.describe_sources([]), {"path": "", "paths": []})

    def test_only_existing_paths_are_converted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "a.txt"
            real.write_text("a")
            got = server.requested_sources(
                {"sources": [str(real), str(Path(tmp) / "missing"), "", 3, None,
                             str(real)]})
            self.assertEqual(got, [real])
            # The single-string form the page used before files could be chosen.
            self.assertEqual(server.requested_sources({"source": str(real)}), [real])
            self.assertEqual(server.requested_sources({"sources": "not a list"}), [])

    def test_the_preview_counts_files_the_way_the_converter_walks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.pdf").write_bytes(b"%PDF")
            (root / "sub").mkdir()
            (root / "sub" / "b.docx").write_bytes(b"PK")
            (root / ".git").mkdir()
            (root / ".git" / "config").write_text("hidden folder")
            (root / ".DS_Store").write_text("hidden file")
            (root / "node_modules").mkdir()
            (root / "node_modules" / "x.js").write_text("nobody means these")
            preview = server.count_candidates([str(root)])
            self.assertEqual(preview["files"], 2)
            self.assertFalse(preview["capped"])
            self.assertEqual({k["ext"] for k in preview["kinds"]}, {".pdf", ".docx"})
            described = server.describe_sources([str(root / "a.pdf")])
            self.assertEqual(described["files"], 1)
            self.assertFalse(described["isFolder"])
            self.assertTrue(server.describe_sources([str(root)])["isFolder"])

    def test_the_outputs_come_from_the_manifest_and_nothing_else(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.json"
            self.assertEqual(server.manifest_outputs(manifest), [])
            manifest.write_text("{not json")
            self.assertEqual(server.manifest_outputs(manifest), [])
            manifest.write_text(json.dumps({"files": [
                {"source": "/s/a.pdf", "output": "/o/a.md", "status": "converted"},
                {"source": "/s/b.pdf", "output": "/o/b.md", "status": "unchanged"},
                {"source": "/s/c.png", "output": None, "status": "skipped"},
                {"source": "/s/d.pdf", "output": "/o/d.md", "status": "failed"},
                "junk"]}))
            self.assertEqual(
                [o["output"] for o in server.manifest_outputs(manifest)],
                ["/o/a.md", "/o/b.md"])


def minimal_pptx(path: Path) -> None:
    """A one-slide deck built by hand, so no engine is needed to read it."""
    ns = ('xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
          'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"')
    slide = (f"<p:sld {ns}><p:cSld><p:spTree>"
             "<p:sp><p:nvSpPr><p:cNvPr id=\"2\" name=\"Title\"/><p:nvPr>"
             "<p:ph type=\"title\"/></p:nvPr></p:nvSpPr><p:txBody><a:p><a:r>"
             "<a:t>Hello slide</a:t></a:r></a:p></p:txBody></p:sp>"
             "<p:sp><p:txBody><a:p><a:r><a:t>A bullet</a:t></a:r></a:p>"
             "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("ppt/slides/slide1.xml", slide)


class SingleFileTest(unittest.TestCase):
    """One file in, Markdown out, nothing written: what an assistant wants."""

    def run_converter(self, *args: str, env: dict | None = None):
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "doc2gfm.py"), *args],
            capture_output=True, timeout=120,
            env={**os.environ, **(env or {})})

    def test_stdout_prints_the_markdown_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            deck = Path(tmp) / "deck.pptx"
            minimal_pptx(deck)
            result = self.run_converter(str(deck), "--stdout")
            self.assertEqual(result.returncode, 0, result.stderr)
            text = result.stdout.decode("utf-8")
            self.assertIn("## Slide 1: Hello slide", text)
            self.assertIn("- A bullet", text)
            self.assertNotIn("source_sha256", text)   # no front matter unasked
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()),
                             ["deck.pptx"])

    def test_stdout_can_keep_the_front_matter_when_asked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.txt"
            note.write_text("plain words\n", encoding="utf-8")
            result = self.run_converter(str(note), "--stdout", "--front-matter")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result.stdout.startswith(b"---\nsource: \"note.txt\""))

    def test_stdout_is_utf8_whatever_the_locale_says(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.txt"
            note.write_text("caf\u00e9 \u2014 na\u00efve\n", encoding="utf-8")
            result = self.run_converter(str(note), "--stdout",
                                        env={"LC_ALL": "C", "LANG": "C",
                                             "PYTHONIOENCODING": "ascii"})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.decode("utf-8").strip(),
                             "caf\u00e9 \u2014 na\u00efve")

    def test_stdout_wants_exactly_one_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.txt"
            a.write_text("a")
            b = Path(tmp) / "b.txt"
            b.write_text("b")
            self.assertEqual(self.run_converter(str(a), str(b), "--stdout").returncode, 2)
            self.assertEqual(self.run_converter(tmp, "--stdout").returncode, 2)
            self.assertEqual(self.run_converter(str(a), "--stdout", "-o", tmp).returncode, 2)

    def test_a_file_it_cannot_read_is_an_error_with_a_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            picture = Path(tmp) / "photo.png"
            picture.write_bytes(b"\x89PNG")
            result = self.run_converter(str(picture), "--stdout")
            self.assertEqual(result.returncode, 1)
            self.assertIn(b"not a document this converter reads", result.stderr)
            self.assertEqual(result.stdout, b"")

    def test_formats_read_without_pandoc_do_not_need_it(self) -> None:
        # PDFs, slides, spreadsheets and text are read by this script itself.
        # A machine without Pandoc still gets those, and is told about the
        # rest per file rather than refused at the door.
        with tempfile.TemporaryDirectory() as tmp:
            deck = Path(tmp) / "deck.pptx"
            minimal_pptx(deck)
            nowhere = str(Path(tmp) / "empty-path")
            result = self.run_converter(str(deck), "--stdout",
                                        env={"PATH": nowhere, "DOC2MD_HOME": tmp})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(b"Hello slide", result.stdout)
            html = Path(tmp) / "page.html"
            html.write_text("<p>hi</p>")
            result = self.run_converter(str(html), "--stdout",
                                        env={"PATH": nowhere, "DOC2MD_HOME": tmp})
            if doc2gfm.find_tool("pandoc") is None:
                self.assertEqual(result.returncode, 1)
                self.assertIn(b"pandoc is not installed", result.stderr)

    def test_a_markdown_file_is_never_written_over_itself(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "notes.md"
            page.write_text("# notes")
            result = self.run_converter(str(page), "-o", tmp)
            self.assertEqual(result.returncode, 2)
            self.assertIn(b"would be overwritten", result.stderr)
            self.assertEqual(page.read_text(), "# notes")

    def test_the_function_the_mcp_server_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.txt"
            note.write_text("from python\n")
            self.assertEqual(doc2gfm.convert_file_to_markdown(note), "from python\n")
            with self.assertRaises(doc2gfm.ConversionError):
                doc2gfm.convert_file_to_markdown(Path(tmp) / "missing.pdf")


class McpServerTest(unittest.TestCase):
    """The converter over MCP: one JSON-RPC message per line, over stdio."""

    def session(self, messages: list[dict]) -> list[dict]:
        process = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "mcp_server.py")],
            input="\n".join(json.dumps(m) for m in messages) + "\n",
            capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(process.returncode, 0, process.stderr)
        return [json.loads(line) for line in process.stdout.splitlines() if line]

    def test_initialize_lists_and_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note.txt"
            note.write_text("read me over mcp\n", encoding="utf-8")
            replies = self.session([
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                            "clientInfo": {"name": "test", "version": "0"}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "convert_document",
                            "arguments": {"path": str(note)}}},
                {"jsonrpc": "2.0", "id": 4, "method": "ping"},
            ])
            by_id = {reply["id"]: reply for reply in replies}
            self.assertEqual(set(by_id), {1, 2, 3, 4})   # no reply to the notification
            init = by_id[1]["result"]
            self.assertEqual(init["protocolVersion"], "2025-03-26")
            self.assertIn("tools", init["capabilities"])
            self.assertIn("convert_document", init["instructions"])
            names = [tool["name"] for tool in by_id[2]["result"]["tools"]]
            self.assertEqual(names, ["convert_document", "convert_folder"])
            for tool in by_id[2]["result"]["tools"]:
                self.assertEqual(tool["inputSchema"]["type"], "object")
            call = by_id[3]["result"]
            self.assertFalse(call["isError"])
            self.assertEqual(call["content"][0]["text"], "read me over mcp\n")
            self.assertEqual(by_id[4]["result"], {})

    def test_a_long_document_comes_in_pieces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "long.txt"
            note.write_text("x" * 5000 + "\n", encoding="utf-8")
            replies = self.session([
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": "convert_document",
                            "arguments": {"path": str(note), "max_chars": 1000}}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                 "params": {"name": "convert_document",
                            "arguments": {"path": str(note), "offset": 4001,
                                          "max_chars": 1000}}},
            ])
            first = replies[0]["result"]["content"][0]["text"]
            self.assertTrue(first.startswith("x" * 1000))
            self.assertIn("offset=1000", first)
            second = replies[1]["result"]["content"][0]["text"]
            self.assertEqual(second, "x" * 999 + "\n")   # the rest, no note

    def test_failures_are_answers_not_crashes(self) -> None:
        replies = self.session([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "convert_document",
                        "arguments": {"path": "/no/such/file.pdf"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "no_such_tool"}},
            {"jsonrpc": "2.0", "id": 3, "method": "no/such/method"},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "convert_folder", "arguments": {}}},
        ])
        by_id = {reply["id"]: reply for reply in replies}
        self.assertTrue(by_id[1]["result"]["isError"])
        self.assertIn("no such file", by_id[1]["result"]["content"][0]["text"])
        self.assertEqual(by_id[2]["error"]["code"], -32602)
        self.assertEqual(by_id[3]["error"]["code"], -32601)
        self.assertTrue(by_id[4]["result"]["isError"])

    def test_a_folder_conversion_returns_the_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "docs"
            source.mkdir()
            (source / "a.txt").write_text("a\n")
            minimal_pptx(source / "deck.pptx")
            output = Path(tmp) / "out"
            replies = self.session([
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": "convert_folder",
                            "arguments": {"source": str(source),
                                          "output": str(output)}}},
            ])
            result = replies[0]["result"]
            self.assertFalse(result["isError"], result)
            self.assertIn("Converted: 2", result["content"][0]["text"])
            self.assertTrue((output / "a.md").is_file())
            self.assertTrue((output / "deck.md").is_file())


if __name__ == "__main__":
    unittest.main()
