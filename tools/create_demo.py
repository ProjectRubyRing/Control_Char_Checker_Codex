"""Generate a small reproducible report and its example inputs."""

import codecs
import importlib.util
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("checker", ROOT / "lib" / "text_checker.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def main():
    directory = ROOT / "examples" / "input"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "clean.txt").write_bytes("通常のテキスト\r\n次の行\r\n".encode())
    text = "NBSP: \u00a0 / NARROW NBSP: \u202f / ZWSP: \u200b / BOM: \ufeff\nTAB:\tCONTROL:\x07\r\n全角空白: \u3000\r"
    (directory / "controls.txt").write_bytes(text.encode())
    (directory / "unicode.md").write_bytes("絵文字: 😀 / 異体字セレクター: \ufe0f\nタグ文字: \U000e0001\n".encode())
    (directory / "settings.ini").write_bytes(codecs.BOM_UTF16_LE + "[settings]\r\nname=sample\r\n".encode("utf-16le"))
    with zipfile.ZipFile(directory / "archive.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("hello.txt", "This archive is classified as binary.\n")
    rules = checker.Rules(ROOT / "data" / "unicode-checks.tsv")
    records = checker.scan_tree(directory, set(), "auto", rules)
    output = ROOT / "examples" / "demo-report.html"
    report = checker.make_report(directory, output, "auto", records, rules)
    # Keep the distributable example independent of the developer's absolute path.
    report["root"] = "examples/input (sample)"
    report["excludedOutputs"] = ["examples/demo-report.html", "examples/demo-report.json"]
    checker.write_reports(report, output, True)
    print("Generated examples/demo-report.html and JSON.")


if __name__ == "__main__":
    main()
