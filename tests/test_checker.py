"""Behavioral checks for both implementations, using actual CLI subprocesses."""

import codecs
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("text_checker", ROOT / "lib" / "text_checker.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class CheckerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = checker.Rules(ROOT / "data" / "unicode-checks.tsv")
        cls.engines = ["python"]
        cls.powershell = shutil.which("powershell") if os.name == "nt" else shutil.which("pwsh")
        if cls.powershell:
            cls.engines.append("powershell")
        if os.name != "nt" and shutil.which("bash"):
            cls.engines.append("shell")

    def setUp(self):
        # Everything stays in the authorized workspace, including Windows lock tests.
        # On Windows, mkdir(0o700) installs an owner-only ACL. In a restricted
        # process that can deny the creator access; inherit the workspace ACL.
        self.work = ROOT / ("checker-test-" + uuid.uuid4().hex)
        self.work.mkdir()
        self.addCleanup(self.cleanup_work)
        self.source = self.work / "対象 [files]"
        self.source.mkdir()
        self.sequence = 0

    def cleanup_work(self):
        resolved = self.work.resolve()
        if resolved.parent != ROOT or not resolved.name.startswith("checker-test-"):
            raise RuntimeError("Refusing cleanup outside the test workspace.")
        shutil.rmtree(resolved)

    def write(self, name, content):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def command(self, engine, output, encoding="auto", force=False, source=None):
        source = source or self.source
        if engine == "powershell":
            return [self.powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                    "-File", str(ROOT / "check-text-files.ps1"), "-Path", str(source),
                    "-Output", str(output), "-Encoding", encoding] + (["-Force"] if force else [])
        prefix = (["bash", str(ROOT / "check-text-files.sh")] if engine == "shell" else
                  [sys.executable, str(ROOT / "lib" / "text_checker.py")])
        return prefix + [str(source), "--output", str(output), "--encoding", encoding] + (["--force"] if force else [])

    def run_engine(self, engine, encoding="auto", expected=0):
        self.sequence += 1
        output = self.work / (engine + str(self.sequence) + ".html")
        process = subprocess.run(self.command(engine, output, encoding), capture_output=True, timeout=120)
        detail = engine + ': ' + process.stderr.decode("utf-8", errors="replace") + process.stdout.decode("utf-8", errors="replace")
        if output.with_suffix(".json").exists():
            detail += json.dumps(json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))["files"], ensure_ascii=True)[:5000]
        self.assertEqual(expected, process.returncode, detail)
        self.assertTrue(output.exists(), process.stderr.decode("utf-8", errors="replace"))
        report = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
        html = output.read_text(encoding="utf-8")
        self.assertIn('font-family: "Meiryo UI"', html)
        self.assertNotIn("@@REPORT_JSON@@", html)
        return report

    def each(self, encoding="auto", expected=0):
        reports = [self.run_engine(engine, encoding, expected) for engine in self.engines]
        # Error messages vary by OS, but all inspection data must agree.
        for report in reports[1:]:
            self.assertEqual(reports[0]["summary"], report["summary"])
            self.assertEqual(reports[0]["complete"], report["complete"])
            baseline = [{key: value for key, value in item.items() if key != "reason"} for item in reports[0]["files"]]
            actual = [{key: value for key, value in item.items() if key != "reason"} for item in report["files"]]
            self.assertEqual(baseline, actual)
        return reports[0]

    def test_empty_directory_and_plain_hidden_extensionless_files(self):
        self.assertEqual(0, self.each()["summary"]["entries"])
        self.write(".hidden", b"plain\r\n")
        self.write("README", b"plain\n")
        self.write("sub dir/日本語 [1].TXT", "日本語\n".encode())
        self.write("really-text.exe", b"MZ this is ordinary text\n")
        self.write("zero.txt", b"")
        report = self.each()
        self.assertEqual(5, report["summary"]["text"])
        files = {item["path"]: item for item in report["files"]}
        self.assertEqual("", files[".hidden"]["extension"])
        self.assertEqual(".TXT", files["sub dir/日本語 [1].TXT"]["extension"])
        self.assertEqual(0, files["zero.txt"]["analyses"][0]["lines"])
        self.assertIsNone(files["zero.txt"]["analyses"][0]["endsWithNewline"])

    def test_all_newlines_and_unicode_scalar_positions(self):
        text = "😀\u00a0\r\nA\u200b\nB\u202f\rC\ufeff\x85D\u2028E\u2029F\U000e0001\U000e0100\ufffd\ufdd0\x00\t"
        self.write("controls.txt", text.encode())
        analysis = self.each(expected=1)["files"][0]["analyses"][0]
        self.assertEqual(dict.fromkeys(("CRLF", "LF", "CR", "NEL", "LS", "PS"), 1), analysis["newlines"])
        self.assertEqual(7, analysis["lines"])
        self.assertEqual(1, analysis["tabCount"])
        findings = {item["codepoint"]: item for item in analysis["findings"]}
        self.assertEqual([[1, 2]], findings["U+00A0"]["positions"])
        self.assertEqual([[2, 2]], findings["U+200B"]["positions"])
        self.assertEqual([[7, 2]], findings["U+E0001"]["positions"])
        self.assertEqual([[7, 3]], findings["U+E0100"]["positions"])
        self.assertEqual("Zs", findings["U+00A0"]["category"])
        self.assertEqual(len(text), analysis["characters"])

    def test_every_bundled_unicode_property_and_noncharacters(self):
        points = set(self.rules.points) | {0xFFFD, 0xFDD0, 0xFDEF, 0xFFFF, 0x10FFFF}
        text = "".join(chr(point) for point in sorted(points))
        self.write("all-unicode.txt", codecs.BOM_UTF8 + text.encode())
        analysis = self.each(expected=1)["files"][0]["analyses"][0]
        expected = {"U+{:04X}".format(point) for point in points if point not in (9, 10, 13, 32)}
        self.assertEqual(expected, {item["codepoint"] for item in analysis["findings"]})
        self.assertEqual(len(text), analysis["characters"])

    def test_no_position_truncation(self):
        self.write("many.txt", ("\u00a0" * 5000).encode())
        finding = self.each(expected=1)["files"][0]["analyses"][0]["findings"][0]
        self.assertEqual(5000, finding["count"])
        self.assertEqual(5000, len(finding["positions"]))
        self.assertEqual([1, 5000], finding["positions"][-1])

    def test_chunk_boundaries_and_findings_far_past_prefix(self):
        text = "a" * 65535 + "\r\n" + "b" * (65536 * 2 - 3) + "\u00a0\n\U000e0001"
        self.write("large.log", text.encode())
        analysis = self.each(expected=1)["files"][0]["analyses"][0]
        self.assertEqual(1, analysis["newlines"]["CRLF"])
        self.assertEqual(1, analysis["newlines"]["LF"])
        self.assertEqual(0, analysis["newlines"]["CR"])
        findings = {item["codepoint"]: item for item in analysis["findings"]}
        self.assertEqual([[2, 65536 * 2 - 2]], findings["U+00A0"]["positions"])
        self.assertEqual([[3, 1]], findings["U+E0001"]["positions"])

    def test_unicode_boms_and_bomless_hints(self):
        text = "ABC 日本語\u00a0\r\n😀\u200b"
        for bom, encoding in checker.BOMS:
            self.write(encoding + ".txt", bom + text.encode(encoding))
        for encoding in ("utf-16le", "utf-16be", "utf-32le", "utf-32be"):
            self.write("no-bom-" + encoding + ".txt", ("ASCII text ABCDE\u00a0\n").encode(encoding))
        report = self.each(expected=1)
        for item in report["files"]:
            self.assertEqual(1, len(item["analyses"]))
            self.assertEqual("heuristic" if item["path"].startswith("no-bom") else "bom", item["encodingSource"])
            self.assertIn("U+00A0", [finding["codepoint"] for finding in item["analyses"][0]["findings"]])

    def test_explicit_legacy_encodings_and_non_ascii_bomless_utf16(self):
        for encoding, text in (("cp932", "日本語\u3000\n"), ("euc-jp", "日本語\u3000\n"),
                               ("iso-2022-jp", "日本語\n"), ("windows-1252", "café\u00a0\n"),
                               ("latin-1", "café\u00a0\x85"), ("utf-16be", "日本語\u00a0")):
            with self.subTest(encoding=encoding):
                self.write("legacy.txt", text.encode(encoding))
                report = self.each(encoding, 0 if encoding == "iso-2022-jp" else 1)
                self.assertEqual("explicit", report["files"][0]["encodingSource"])
                self.assertEqual([encoding], report["files"][0]["encodings"])

    def test_ambiguous_encoding_includes_western_nbsp(self):
        self.write("western.txt", b"cafe\xa0\n")
        report = self.each(expected=2)
        self.assertFalse(report["complete"])
        self.assertEqual(1, report["summary"]["ambiguous"])
        file = report["files"][0]
        self.assertIn("windows-1252", file["encodings"])
        western = next(item for item in file["analyses"] if item["encoding"] == "windows-1252")
        self.assertEqual("U+00A0", western["findings"][0]["codepoint"])

    def test_all_valid_pinned_legacy_mappings(self):
        tables = {name: [] for name in ("cp932", "euc-jp", "windows-1252")}
        for line in (ROOT / "data" / "legacy-mappings.tsv").read_text().splitlines():
            if line.startswith("#"):
                continue
            name, encoded, point = line.split("\t")
            tables[name].append((bytes.fromhex(encoded), int(point, 16)))
        for name, values in tables.items():
            with self.subTest(encoding=name):
                data = b"".join(encoded for encoded, _ in values)
                self.write("mappings.txt", data)
                analysis = self.each(name, 1)["files"][0]["analyses"][0]
                self.assertEqual(len(values), analysis["characters"])
                self.assertEqual("".join(chr(point) for _, point in values), data.decode(name))

    def test_strict_legacy_rejects_invalid_sequences(self):
        cases = (("cp932", b"\x81"), ("cp932", b"\x81\x00"),
                 ("euc-jp", b"\xa0"), ("euc-jp", b"\x8f\xa2"),
                 ("windows-1252", b"\x81"), ("iso-2022-jp", b"\x1b$B$"),
                 ("iso-2022-jp", b"\x1b(x"))
        for name, data in cases:
            with self.subTest(encoding=name, data=data):
                self.write("invalid-legacy.txt", data)
                self.assertEqual(1, self.each(name, 2)["summary"]["error"])

    def test_iso2022_keeps_controls_roman_and_revision_sequences(self):
        data = b"\x0eA\x0f\x1b(J\\~\x1b(B\x1b&@\x1b$B$\x22\x1b(B\x1bfoo\n"
        self.write("jis.txt", data)
        analysis = self.each("iso-2022-jp", 1)["files"][0]["analyses"][0]
        self.assertEqual(len(data.decode("iso-2022-jp")), analysis["characters"])
        self.assertEqual({"U+000E", "U+000F", "U+001B"}, {item["codepoint"] for item in analysis["findings"]})

    def test_iso2022_marker_after_first_chunk(self):
        self.write("jis.txt", b"a" * 65535 + "日本語\n".encode("iso-2022-jp"))
        report = self.each()
        self.assertEqual(["iso-2022-jp"], report["files"][0]["encodings"])

    def test_known_binary_signatures_regardless_of_extension(self):
        self.write("image.txt", b"\x89PNG\r\n\x1a\n" + bytes(range(256)))
        self.write("archive.docx", b"PK\x03\x04" + bytes(range(256)))
        report = self.each()
        self.assertEqual(2, report["summary"]["binary"])
        self.assertEqual(0, report["summary"]["text"])

    def test_invalid_byte_sequences_at_end_and_bom_conflicts(self):
        for encoding, data in (("utf-8", codecs.BOM_UTF8 + b"a" * 140000 + b"\xff"),
                               ("utf-16le", codecs.BOM_UTF16_LE + b"A\x00\xff"),
                               ("utf-32be", codecs.BOM_UTF32_BE + b"\x00\x00\x00A\x00"),
                               ("utf-16le", codecs.BOM_UTF16_LE + b"\x00\xd8")):
            with self.subTest(encoding=encoding, data_length=len(data)):
                self.write("invalid.txt", data)
                report = self.each(expected=2)
                self.assertEqual(1, report["summary"]["error"])
                self.assertFalse(report["complete"])
        self.write("invalid.txt", codecs.BOM_UTF16_LE + "hello".encode("utf-16le"))
        self.assertEqual(1, self.each("utf-8", 2)["summary"]["error"])

    def test_output_overwrite_guard_and_exclusion(self):
        self.write("original.txt", b"original\n")
        for engine in self.engines:
            with self.subTest(engine=engine):
                output = self.source / (engine + ".html")
                command = self.command(engine, output)
                process = subprocess.run(command, capture_output=True, timeout=120)
                self.assertEqual(0, process.returncode, process.stderr)
                saved = output.read_bytes()
                process = subprocess.run(command, capture_output=True, timeout=120)
                self.assertEqual(2, process.returncode)
                self.assertEqual(saved, output.read_bytes())
                process = subprocess.run(self.command(engine, output, force=True), capture_output=True, timeout=120)
                self.assertEqual(0, process.returncode, process.stderr)
                report = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
                self.assertNotIn(output.name, [item["path"] for item in report["files"]])
                self.assertNotIn(output.with_suffix(".json").name, [item["path"] for item in report["files"]])
                output.unlink()
                output.with_suffix(".json").unlink()
        self.assertFalse(list(self.source.glob(".text-check-*.tmp")))
        self.assertEqual(b"original\n", (self.source / "original.txt").read_bytes())

    def test_bad_cli_inputs_create_no_report(self):
        for engine in self.engines:
            for source, filename in ((self.work / "missing", "missing.html"), (self.source, "bad.json")):
                output = self.work / filename
                process = subprocess.run(self.command(engine, output, source=source), capture_output=True, timeout=120)
                self.assertEqual(2, process.returncode, process.stderr)
                self.assertFalse(output.exists())

    @unittest.skipUnless(os.name == "nt", "Windows file-sharing behavior")
    def test_locked_file_is_not_reported_as_clean(self):
        path = self.write("locked.txt", b"locked\n")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                      ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        kernel.CreateFileW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0, None)
        self.assertNotEqual(ctypes.c_void_p(-1).value, handle)
        try:
            report = self.each(expected=2)
            self.assertEqual("error", report["files"][0]["status"])
        finally:
            kernel.CloseHandle(handle)

    def test_symlink_is_visible_and_not_followed(self):
        target = self.write("target.txt", b"plain\n")
        try:
            (self.source / "link.txt").symlink_to(target)
            (self.source / "cycle").symlink_to(self.source, target_is_directory=True)
        except OSError as error:
            self.skipTest("Symlink creation is unavailable: " + str(error))
        report = self.each(expected=2)
        self.assertEqual(2, report["summary"]["skipped"])
        self.assertEqual(1, report["summary"]["text"])

    def test_unreadable_directory_is_reported_python(self):
        with patch.object(checker.os, "scandir", side_effect=PermissionError("denied")):
            results = checker.scan_tree(self.source, set(), "auto", self.rules)
        self.assertEqual(1, len(results))
        self.assertEqual("error", results[0]["status"])
        self.assertEqual(".", results[0]["path"])

    def test_file_changed_during_inspection_is_reported_python(self):
        path = self.write("changed.txt", b"plain\n")
        original = checker.scan_decoded
        def change_after_scan(*args):
            result = original(*args)
            path.write_bytes(b"changed content\n")
            return result
        with patch.object(checker, "scan_decoded", side_effect=change_after_scan):
            result = checker.check_file(path, self.source, "auto", self.rules)
        self.assertEqual("error", result["status"])
        self.assertIn("changed", result["reason"])

    def test_report_data_cannot_break_out_of_script_and_js_has_valid_syntax(self):
        output = self.work / "safe.html"
        attack = "</script><script>alert('x')</script>&"
        report = checker.make_report(self.source, output, "auto", [], self.rules)
        report["root"] = attack
        checker.write_reports(report, output, False)
        html = output.read_text(encoding="utf-8")
        self.assertNotIn(attack, html)
        data = html.split('<script id="report-data" type="application/json">', 1)[1].split("</script>", 1)[0]
        self.assertEqual(attack, json.loads(data)["root"])
        self.assertEqual(attack, json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))["root"])
        node = shutil.which("node")
        if node:
            script = html.split("  <script>\n", 1)[1].split("</script>", 1)[0]
            js_path = self.work / "report.js"
            js_path.write_text(script, encoding="utf-8")
            result = subprocess.run([node, "--check", str(js_path)], capture_output=True, timeout=20)
            self.assertEqual(0, result.returncode, result.stderr)

    def test_report_ui_rendering_and_filters(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is unavailable; browser behavior test skipped.")
        self.write("warning.txt", "NBSP: \u00a0\n".encode())
        self.write("clean.txt", b"clean\n")
        self.each(expected=1)
        for output in self.work.glob("*.html"):
            result = subprocess.run([node, str(ROOT / "tests" / "test_report_ui.js"), str(output)], capture_output=True, timeout=20)
            self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()
