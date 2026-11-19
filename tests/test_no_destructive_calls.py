import re
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "wsmigrate"
SOURCES = {p: p.read_text().splitlines() for p in PKG.rglob("*.py")}


def test_no_delete_trash_or_modify_in_package():
    hits = [(p, n) for p, lines in SOURCES.items() for n, l in enumerate(lines)
            if re.search(r"\.(delete|trash|untrash|modify|batchDelete|batchModify)\(", l)]
    assert len(hits) == 1
    path, n = hits[0]
    before = SOURCES[path][:n]
    assert path.name == "api.py" and ".events().delete(" in SOURCES[path][n]
    assert [l for l in before if l.lstrip().startswith("def ")][-1].strip().startswith("def delete_event(")
    assert [l for l in before if l.startswith("class ")][-1].startswith("class Calendar")


def test_no_print_or_logging_in_package():
    assert SOURCES  # the scan found the package
    for p, lines in SOURCES.items():
        for l in lines:
            assert not re.search(r"\bprint\(", l), p.name
            assert not re.search(r"^\s*(import|from) logging\b", l), p.name
