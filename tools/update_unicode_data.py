"""Regenerate the bundled Unicode properties; not needed to run the checker."""

import hashlib
from pathlib import Path
import urllib.request


VERSION = "17.0.0"
BASE = "https://www.unicode.org/Public/{}/ucd/".format(VERSION)
ROOT = Path(__file__).resolve().parents[1]


def download(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def ranges(points):
    start = previous = None
    for point in sorted(points):
        if previous is not None and point != previous + 1:
            yield start, previous
            start = None
        if start is None:
            start = point
        previous = point
    if start is not None:
        yield start, previous


def main():
    sources = {name: download(BASE + name) for name in (
        "UnicodeData.txt", "DerivedCoreProperties.txt", "NameAliases.txt")}
    properties = {name: set() for name in ("Cc", "Cf", "Zs", "Zl", "Zp", "DI")}
    characters = {}
    for line in sources["UnicodeData.txt"].decode("utf-8").splitlines():
        fields = line.split(";")
        point = int(fields[0], 16)
        characters[point] = (fields[2], fields[1])
        if fields[2] in properties:
            properties[fields[2]].add(point)
    for line in sources["DerivedCoreProperties.txt"].decode("utf-8").splitlines():
        fields = line.split("#", 1)[0].strip().split(";")
        if len(fields) < 2 or fields[1].strip() != "Default_Ignorable_Code_Point":
            continue
        bounds = fields[0].strip().split("..")
        first, last = int(bounds[0], 16), int(bounds[-1], 16)
        properties["DI"].update(range(first, last + 1))
    for line in sources["NameAliases.txt"].decode("utf-8").splitlines():
        fields = line.split("#", 1)[0].strip().split(";")
        if len(fields) == 3 and fields[2] == "control":
            point = int(fields[0], 16)
            category, _ = characters[point]
            characters[point] = (category, fields[1])
    selected = set().union(*properties.values()) | {0xFFFD}
    lines = ["# Derived from Unicode {}; see LICENSE-UNICODE.txt".format(VERSION),
             "version\t" + VERSION]
    for name, content in sources.items():
        lines.append("# {} SHA256 {}".format(BASE + name, hashlib.sha256(content).hexdigest()))
    for prop, points in properties.items():
        for first, last in ranges(points):
            lines.append("range\t{}\t{:X}\t{:X}".format(prop, first, last))
    for point in sorted(selected):
        if point in characters:
            category, name = characters[point]
            if name == "<control>":
                name = "CONTROL"
            lines.append("name\t{:X}\t{}\t{}".format(point, category, name))
    data_dir = ROOT / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "unicode-checks.tsv").write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    (ROOT / "LICENSE-UNICODE.txt").write_bytes(download("https://www.unicode.org/license.txt"))
    print("Generated Unicode {} properties and names.".format(VERSION))


if __name__ == "__main__":
    main()
