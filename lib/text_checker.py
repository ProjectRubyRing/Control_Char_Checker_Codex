"""Streaming text inspection for Python 3.9+; no third-party dependencies."""

import argparse
import codecs
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import sys
import tempfile


TOOL_ROOT = Path(__file__).resolve().parents[1]
CHUNK_SIZE = 65536
ENCODINGS = ("auto", "utf-8", "utf-16le", "utf-16be", "utf-32le", "utf-32be",
             "cp932", "euc-jp", "iso-2022-jp", "windows-1252", "latin-1")
BOMS = ((b"\xff\xfe\x00\x00", "utf-32le"), (b"\x00\x00\xfe\xff", "utf-32be"),
        (b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16le"),
        (b"\xfe\xff", "utf-16be"))
ISO_MARKERS = (b"\x1b$B", b"\x1b$@", b"\x1b(J", b"\x1b(B")
MAGICS = ((b"\x89PNG\r\n\x1a\n", "PNG"), (b"GIF87a", "GIF"), (b"GIF89a", "GIF"),
          (b"\xff\xd8\xff", "JPEG"), (b"%PDF-", "PDF"), (b"PK\x03\x04", "ZIP"),
          (b"PK\x05\x06", "ZIP"), (b"PK\x07\x08", "ZIP"), (b"\x7fELF", "ELF"),
          (b"\x1f\x8b", "gzip"), (b"BZh", "bzip2"), (b"\xfd7zXZ\x00", "xz"),
          (b"7z\xbc\xaf\x27\x1c", "7z"), (b"Rar!\x1a\x07", "RAR"),
          (b"SQLite format 3\x00", "SQLite"),
          (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "OLE"),
          (b"II\x2a\x00", "TIFF"), (b"MM\x00\x2a", "TIFF"))


class Rules:
    def __init__(self, path):
        self.points = {}
        names = {}
        kinds = {"Cc": "control", "Cf": "format", "Zs": "space",
                 "Zl": "separator", "Zp": "separator", "DI": "ignorable"}
        self.version = ""
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                fields = line.rstrip("\n").split("\t")
                if fields[0] == "version":
                    self.version = fields[1]
                elif fields[0] == "range":
                    for point in range(int(fields[2], 16), int(fields[3], 16) + 1):
                        self.points.setdefault(point, (kinds[fields[1]], fields[1]))
                elif fields[0] == "name":
                    names[int(fields[1], 16)] = (fields[2], fields[3])
        for point, (kind, category) in list(self.points.items()):
            category, name = names.get(point, ("Cn", "UNASSIGNED DEFAULT IGNORABLE"))
            self.points[point] = (kind, category, name)

    def classify(self, point):
        if point in (9, 10, 13, 32):
            return None
        if point == 0xFFFD:
            return "replacement", "So", "REPLACEMENT CHARACTER"
        if 0xFDD0 <= point <= 0xFDEF or (point & 0xFFFF) in (0xFFFE, 0xFFFF):
            return "noncharacter", "Cn", "UNICODE NONCHARACTER"
        return self.points.get(point)


def scan_decoded(stream, encoding, offset, rules):
    """Validate every byte and track Unicode scalar columns across chunk boundaries."""
    stream.seek(offset)
    decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
    newlines = dict.fromkeys(("CRLF", "LF", "CR", "NEL", "LS", "PS"), 0)
    findings = {}
    characters = tabs = 0
    line = column = 1
    previous_cr = ends_with_newline = False
    while True:
        data = stream.read(CHUNK_SIZE)
        text = decoder.decode(data, final=not data)
        for character in text:
            point = ord(character)
            characters += 1
            rule = rules.classify(point)
            if rule:
                if point not in findings:
                    kind, category, name = rule
                    findings[point] = {"codepoint": "U+{:04X}".format(point), "name": name,
                                       "category": category, "kind": kind, "count": 0,
                                       "positions": []}
                finding = findings[point]
                finding["count"] += 1
                finding["positions"].append([line, column])
            if point == 9:
                tabs += 1
            if point == 13:
                newlines["CR"] += 1
                line += 1
                column = 1
            elif point == 10:
                if previous_cr:
                    newlines["CR"] -= 1
                    newlines["CRLF"] += 1
                else:
                    newlines["LF"] += 1
                    line += 1
                    column = 1
            elif point in (0x85, 0x2028, 0x2029):
                newlines[{0x85: "NEL", 0x2028: "LS", 0x2029: "PS"}[point]] += 1
                line += 1
                column = 1
            else:
                column += 1
            previous_cr = point == 13
            ends_with_newline = point in (10, 13, 0x85, 0x2028, 0x2029)
        if not data:
            break
    breaks = sum(newlines.values())
    return {"encoding": encoding, "characters": characters,
            "lines": breaks + int(bool(characters) and not ends_with_newline),
            "newlines": newlines, "endsWithNewline": ends_with_newline if characters else None,
            "tabCount": tabs, "findings": [findings[point] for point in sorted(findings)]}


def probe(stream):
    stream.seek(0)
    head = stream.read(CHUNK_SIZE)
    all_ascii = True
    has_iso = False
    tail = b""
    chunk = head
    while chunk:
        all_ascii = all_ascii and chunk.isascii()
        has_iso = has_iso or any(marker in tail + chunk for marker in ISO_MARKERS)
        tail = chunk[-3:]
        chunk = stream.read(CHUNK_SIZE)
    return head, all_ascii, has_iso


def binary_format(stream, head):
    for signature, label in MAGICS:
        if head.startswith(signature):
            return label
    if head[:4] == b"RIFF" and head[8:12] in (b"WEBP", b"WAVE", b"AVI "):
        return "RIFF/" + head[8:12].decode("ascii").strip()
    if head[:2] == b"MZ" and len(head) >= 64:
        stream.seek(int.from_bytes(head[60:64], "little"))
        if stream.read(4) == b"PE\x00\x00":
            return "PE"
    return None


def unicode_hint(head):
    # BOM-less UTF-16/32 with predominantly ASCII content. Validation still covers EOF.
    if len(head) < 8:
        return None
    sample = head[:4096]
    zeros = lambda lane, width: sample[lane::width].count(0) / len(sample[lane::width])
    if zeros(2, 4) >= .9 and zeros(3, 4) >= .9 and zeros(1, 4) >= .6 and zeros(0, 4) < .2:
        return "utf-32le"
    if zeros(0, 4) >= .9 and zeros(1, 4) >= .9 and zeros(2, 4) >= .6 and zeros(3, 4) < .2:
        return "utf-32be"
    if zeros(1, 2) >= .6 and zeros(0, 2) < .2:
        return "utf-16le"
    if zeros(0, 2) >= .6 and zeros(1, 2) < .2:
        return "utf-16be"
    return None


def extension(path):
    name = os.path.basename(path)
    index = name.rfind(".")
    return name[index:] if 0 < index < len(name) - 1 else ""


def record(path, root, kind, reason="", size=None):
    return {"path": os.path.relpath(path, root).replace(os.sep, "/"),
            "extension": extension(path), "size": size, "kind": kind,
            "status": {"text": "ok", "binary": "binary", "skipped": "skipped"}.get(kind, "error"),
            "reason": reason, "bom": "", "encodingSource": "", "encodings": [], "analyses": []}


def identity(info):
    result = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    # Windows CRT fstat and path stat can disagree about deprecated st_ctime.
    # File identity, length and mtime remain comparable; POSIX also uses ctime.
    return result if os.name == "nt" else result + (info.st_ctime_ns,)


def check_file(path, root, requested_encoding, rules):
    result = record(path, root, "text")
    try:
        # O_NOFOLLOW also protects the gap between directory enumeration and opening.
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) |
                             getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise OSError("The entry is no longer a regular file.")
            result["size"] = before.st_size
            head, all_ascii, has_iso = probe(stream)
            binary = binary_format(stream, head)
            if binary:
                result.update(kind="binary", status="binary", reason="Known binary signature: " + binary)
            else:
                offset = 0
                bom_encoding = ""
                for signature, encoding in BOMS:
                    if head.startswith(signature):
                        bom_encoding, offset = encoding, len(signature)
                        break
                result["bom"] = bom_encoding
                if requested_encoding != "auto":
                    if bom_encoding and requested_encoding != bom_encoding:
                        raise ValueError("Requested encoding conflicts with BOM ({}).".format(bom_encoding))
                    candidates, source = [requested_encoding], "explicit"
                elif bom_encoding:
                    candidates, source = [bom_encoding], "bom"
                elif unicode_hint(head):
                    candidates, source = [unicode_hint(head)], "heuristic"
                elif has_iso and all_ascii:
                    candidates, source = ["iso-2022-jp"], "iso-2022-marker"
                else:
                    candidates, source = ["utf-8"], "utf-8-validation"
                analyses = []
                try:
                    analyses.append(scan_decoded(stream, candidates[0], offset, rules))
                except UnicodeError:
                    if source != "utf-8-validation":
                        raise
                    # All surviving interpretations are checked, including Western NBSP.
                    source = "ambiguous"
                    for candidate in ("cp932", "euc-jp", "windows-1252", "latin-1"):
                        try:
                            analyses.append(scan_decoded(stream, candidate, 0, rules))
                        except UnicodeError:
                            pass  # This candidate is invalid; other candidates remain visible.
                if not analyses:
                    raise ValueError("No supported strict decoder accepted the file.")
                if source == "utf-8-validation" and all_ascii:
                    analyses[0]["encoding"] = "ascii"
                result["analyses"] = analyses
                result["encodings"] = [analysis["encoding"] for analysis in analyses]
                result["encodingSource"] = source
                if source == "ambiguous":
                    result["reason"] = "Encoding is ambiguous; all listed interpretations were checked. Specify --encoding."
                if source == "ambiguous" or any(
                        analysis["findings"] or sum(value > 0 for value in analysis["newlines"].values()) > 1
                        for analysis in analyses):
                    result["status"] = "warning"
            if identity(before) != identity(os.fstat(stream.fileno())) or identity(before) != identity(os.stat(path)):
                raise OSError("The file changed during inspection; rerun on a stable directory.")
    except (OSError, UnicodeError, ValueError, OverflowError) as error:
        result.update(kind="error", status="error", reason=str(error))
    return result


def scan_tree(root, excluded, encoding, rules):
    results = []
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                children = sorted(entries, key=lambda entry: entry.name)
        except OSError as error:
            results.append(record(directory, root, "error", str(error)))
            continue
        for entry in children:
            path = Path(entry.path)
            if os.path.normcase(os.path.abspath(path)) in excluded:
                continue
            try:
                info = entry.stat(follow_symlinks=False)
                if entry.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                    results.append(record(path, root, "skipped", "Symbolic link/reparse point; target not followed."))
                elif stat.S_ISDIR(info.st_mode):
                    pending.append(path)
                elif stat.S_ISREG(info.st_mode):
                    results.append(check_file(path, root, encoding, rules))
                else:
                    results.append(record(path, root, "skipped", "Not a regular file (device, socket or FIFO)."))
            except OSError as error:
                results.append(record(path, root, "error", str(error)))
    return sorted(results, key=lambda result: result["path"])


def make_report(root, output, encoding, records, rules):
    summary = dict.fromkeys(("entries", "text", "binary", "skipped", "ok", "warning", "error", "ambiguous"), 0)
    summary["entries"] = len(records)
    for result in records:
        if result["kind"] in ("text", "binary", "skipped"):
            summary[result["kind"]] += 1
        if result["status"] in ("ok", "warning", "error"):
            summary[result["status"]] += 1
        if result["encodingSource"] == "ambiguous":
            summary["ambiguous"] += 1
    complete = not (summary["error"] or summary["skipped"] or summary["ambiguous"])
    return {"schemaVersion": 1, "unicodeVersion": rules.version, "root": str(root),
            "generatedAt": datetime.now(timezone.utc).isoformat(), "requestedEncoding": encoding,
            "complete": complete, "excludedOutputs": [str(output), str(output.with_suffix(".json"))],
            "summary": summary, "files": records}


def write_reports(report, output, force):
    json_output = output.with_suffix(".json")
    payload = json.dumps(report, ensure_ascii=True, separators=(",", ":"))
    embedded = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template = (TOOL_ROOT / "assets" / "report.html").read_text(encoding="utf-8")
    if template.count("@@REPORT_JSON@@") != 1:
        raise ValueError("The report template must contain exactly one data placeholder.")
    contents = ((output, template.replace("@@REPORT_JSON@@", embedded)), (json_output, payload + "\n"))
    prepared = []
    try:
        for path, content in contents:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".text-check-", suffix=".tmp", dir=path.parent)
            prepared.append((Path(temporary), path))
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
        for temporary, path in prepared:
            if force:
                os.replace(temporary, path)
            else:
                # Publish without clobbering a path created concurrently after preflight.
                os.link(temporary, path)
                temporary.unlink()
    finally:
        for temporary, _ in prepared:
            if temporary.exists():
                temporary.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inspect all regular files and create monochrome HTML + JSON reports.")
    parser.add_argument("path", help="Directory to inspect recursively, including hidden files.")
    parser.add_argument("--output", default="text-check-report.html", help="HTML output path (JSON uses the same stem).")
    parser.add_argument("--encoding", choices=ENCODINGS, default="auto", help="Explicit decoder or automatic inference.")
    parser.add_argument("--force", action="store_true", help="Allow replacing existing reports.")
    args = parser.parse_args(argv)
    try:
        root = Path(args.path).resolve(strict=True)
        output = Path(os.path.abspath(args.output))
        json_output = output.with_suffix(".json")
        if not root.is_dir():
            raise ValueError("The input path must be a directory.")
        if output.suffix.lower() != ".html":
            raise ValueError("--output must end with .html.")
        for target in (output, json_output):
            if target.is_symlink() or target.is_dir():
                raise ValueError("Output is a directory or symbolic link: " + str(target))
            if target.exists() and not args.force:
                raise ValueError("Output exists; use --force to replace it: " + str(target))
        rules = Rules(TOOL_ROOT / "data" / "unicode-checks.tsv")
        excluded = {os.path.normcase(str(target)) for target in (output, json_output)}
        records = scan_tree(root, excluded, args.encoding, rules)
        report = make_report(root, output, args.encoding, records, rules)
        write_reports(report, output, args.force)
        print("HTML: " + str(output))
        print("JSON: " + str(json_output))
        print("Entries={entries} Text={text} Binary={binary} Review={warning} Errors={error} Skipped={skipped}".format(**report["summary"]))
        return 2 if not report["complete"] else (1 if report["summary"]["warning"] else 0)
    except (OSError, ValueError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
