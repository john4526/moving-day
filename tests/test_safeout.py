import io
import json

import pytest

from wsmigrate import safeout
from wsmigrate.mapping import MappingError


def out(obj):
    s = io.StringIO()
    safeout.emit(obj, s)
    return s.getvalue()


def test_emits_one_json_line():
    text = out({"command": "run", "ok": True, "found": 3, "reason": None})
    assert text.endswith("\n") and text.count("\n") == 1
    assert json.loads(text) == {"command": "run", "ok": True, "found": 3, "reason": None}


def test_unknown_key_rejected():
    with pytest.raises(safeout.UnsafeOutput):
        out({"subject": "x"})


def test_string_with_space_rejected():
    with pytest.raises(safeout.UnsafeOutput):
        out({"name": "a b"})


def test_string_with_at_sign_rejected():
    with pytest.raises(safeout.UnsafeOutput):
        out({"name": "a@b.com"})


def test_nested_list_checked():
    with pytest.raises(safeout.UnsafeOutput):
        out({"users": [{"user": "u001"}, {"subject": "x"}]})


def test_failures_map_allows_reason_keys():
    assert json.loads(out({"failures": {"quota_exceeded": 2}})) == {"failures": {"quota_exceeded": 2}}
    with pytest.raises(safeout.UnsafeOutput):
        out({"failures": {"bad key": 2}})
    with pytest.raises(safeout.UnsafeOutput):
        out({"failures": {"ok_key": "x"}})


def test_nothing_written_when_rejected():
    s = io.StringIO()
    with pytest.raises(safeout.UnsafeOutput):
        safeout.emit({"ok": True, "name": "a b"}, s)
    assert s.getvalue() == ""


def test_reason_for_unknown_exception_has_no_message():
    assert safeout.reason_for(ValueError("secret text")) == "internal_ValueError"


def test_reason_for_uses_reason_attribute():
    assert safeout.reason_for(MappingError("bad_key", 3)) == "bad_key"
