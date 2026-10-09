"""Pin strict legacy mappings used by the Windows implementation."""

import itertools
from pathlib import Path
import sys
import urllib.request


ROOT = Path(__file__).resolve().parents[1]


def candidates(encoding):
    for value in range(256):
        yield bytes([value])
    if encoding == "cp932":
        for lead in itertools.chain(range(0x81, 0xA0), range(0xE0, 0xFD)):
            for trail in range(256):
                yield bytes([lead, trail])
    elif encoding == "euc-jp":
        for trail in range(0xA1, 0xE0):
            yield bytes([0x8E, trail])
        for lead, trail in itertools.product(range(0xA1, 0xFF), repeat=2):
            yield bytes([lead, trail])
            yield bytes([0x8F, lead, trail])


def main():
    lines = ["# Strict CPython codec mappings; see LICENSE-PYTHON.txt",
             "# Generated with Python " + sys.version.split()[0],
             "# codec\tencoded bytes (hex)\tUnicode code point (hex)"]
    for encoding in ("cp932", "euc-jp", "windows-1252"):
        for data in candidates(encoding):
            try:
                text = data.decode(encoding, errors="strict")
            except UnicodeError:
                continue
            if len(text) == 1:
                lines.append("{}\t{}\t{:X}".format(encoding, data.hex().upper(), ord(text)))
    target = ROOT / "data" / "legacy-mappings.tsv"
    target.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    with urllib.request.urlopen("https://raw.githubusercontent.com/python/cpython/v3.12.10/LICENSE", timeout=60) as response:
        (ROOT / "LICENSE-PYTHON.txt").write_bytes(response.read())
    print("Generated {} legacy mappings.".format(len(lines) - 3))


if __name__ == "__main__":
    main()
