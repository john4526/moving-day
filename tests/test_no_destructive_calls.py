import re
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "movingday"
SOURCES = {p: p.read_text().splitlines() for p in PKG.rglob("*.py")}


def _owner(path, n):
    before = SOURCES[path][:n]
    cls = [l for l in before if l.startswith("class ")][-1].split("(")[0].split()[1]
    fn = [l for l in before if l.lstrip().startswith("def ")][-1].strip().split("(")[0].split()[1]
    return path.name, cls, fn


def test_no_delete_trash_or_modify_in_package():
    hits = [(_owner(p, n), l.strip()) for p, lines in SOURCES.items() for n, l in enumerate(lines)
            if re.search(r"\.(delete|trash|untrash|modify|batchDelete|batchModify)\(", l)]
    kinds = (".events().delete(", ".permissions().delete(", ".messages().modify(")
    # the one caller of Drive.trash: drive_copy._close_gone, on the destination Drive only
    callers = [(o[0], l) for o, l in hits if o[0] != "api.py"]
    assert callers == [("drive_copy.py", 'dst.trash(dst_user, did)')]
    hits = [h for h in hits if h[0][0] == "api.py"]
    assert len(hits) == 3
    assert {(o, next(k for k in kinds if k in l)) for o, l in hits} == {
        (("api.py", "Calendar", "delete_event"), kinds[0]), (("api.py", "Drive", "remove_permission"), kinds[1]),
        (("api.py", "Gmail", "set_labels"), kinds[2])}


def test_gmail_set_labels_only_on_the_destination():
    calls = [l.strip() for lines in SOURCES.values() for l in lines if "set_labels(" in l and "def " not in l]
    assert calls == ["dst.set_labels(dst_user, mid, add, remove)"]


def test_trashed_true_only_in_drive_trash():
    hits = [_owner(p, n) for p, lines in SOURCES.items() for n, l in enumerate(lines) if '"trashed": True' in l]
    assert hits == [("api.py", "Drive", "trash")]


def test_no_print_or_logging_in_package():
    assert SOURCES  # the scan found the package
    for p, lines in SOURCES.items():
        for l in lines:
            assert not re.search(r"\bprint\(", l), p.name
            assert not re.search(r"^\s*(import|from) logging\b", l), p.name
