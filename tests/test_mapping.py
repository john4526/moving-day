import os

import pytest

from wsmigrate.mapping import MappingError, load_mapping

HEADER = "key,source,destination,batch\n"


def write(tmp_path, body, header=HEADER, encoding="utf-8"):
    p = tmp_path / "map.csv"
    p.write_text(header + body, encoding=encoding)
    return str(p)


def fails(tmp_path, body, reason, row, header=HEADER):
    with pytest.raises(MappingError) as e:
        load_mapping(write(tmp_path, body, header))
    assert e.value.reason == reason
    assert e.value.row == row


def test_valid_file_loads(tmp_path):
    m = load_mapping(write(tmp_path,
        "u001,sam@old.org,sam@new.org,b1\n"
        "u002,amy@old.org,amy@new.org,b1\n"
        "u003,bo@old.org,bo@new.org,b2\n"))
    assert len(m.users) == 3
    assert [u.key for u in m.batch("b1")] == ["u001", "u002"]
    assert m.by_key("u003").destination == "bo@new.org"
    assert m.dest_for("SAM@old.org") == "sam@new.org"
    assert m.dest_for("nobody@old.org") is None


def test_missing_column(tmp_path):
    fails(tmp_path, "u001,a@x.org,b@y.org\n", "missing_column", 1,
          header="key,source,destination\n")


def test_bad_key(tmp_path):
    fails(tmp_path, "u001,a@x.org,b@y.org,b1\nu12,c@x.org,d@y.org,b1\n", "bad_key", 3)


@pytest.mark.parametrize("src", ["", "nope", "a@@x.org", "a@b@x.org"])
def test_bad_address(tmp_path, src):
    fails(tmp_path, f"u001,{src},b@y.org,b1\n", "bad_address", 2)


def test_duplicate_key(tmp_path):
    fails(tmp_path, "u001,a@x.org,a@y.org,b1\nu001,c@x.org,c@y.org,b1\n", "duplicate_key", 3)


def test_duplicate_source(tmp_path):
    fails(tmp_path, "u001,a@x.org,a@y.org,b1\nu002,a@x.org,c@y.org,b1\n", "duplicate_source", 3)


def test_duplicate_destination(tmp_path):
    fails(tmp_path, "u001,a@x.org,a@y.org,b1\nu002,c@x.org,a@y.org,b1\n", "duplicate_destination", 3)


def test_same_source_and_destination(tmp_path):
    fails(tmp_path, "u001,a@x.org,a@x.org,b1\n", "same_source_and_destination", 2)


def test_empty_batch(tmp_path):
    fails(tmp_path, "u001,a@x.org,a@y.org,\n", "empty_batch", 2)


def test_excel_export_is_normalised(tmp_path):
    p = tmp_path / "map.csv"
    p.write_bytes(b"\xef\xbb\xbf" + (HEADER + "u001, Sam@Old.org ,Sam@New.org, b1 \n").encode())
    m = load_mapping(str(p))
    assert m.users[0].source == "sam@old.org"
    assert m.users[0].destination == "sam@new.org"
    assert m.users[0].batch == "b1"


def test_duplicates_differing_only_by_case_rejected(tmp_path):
    fails(tmp_path, "u001,sam@old.org,a@y.org,b1\nu002,SAM@old.org,b@y.org,b1\n",
          "duplicate_source", 3)


@pytest.mark.parametrize("batch", ["b 1", "b/1", "<b>", "x" * 41, "bé"])
def test_bad_batch(tmp_path, batch):
    fails(tmp_path, f"u001,a@x.org,a@y.org,{batch}\n", "bad_batch", 2)


def test_batch_with_dot_dash_underscore_accepted(tmp_path):
    assert load_mapping(write(tmp_path, f"u001,a@x.org,a@y.org,Wave_1.a-{'x' * 31}\n")).users[0].batch


def test_mapping_writable_by_group_refused(tmp_path):
    p = write(tmp_path, "u001,a@x.org,a@y.org,b1\n")
    os.chmod(p, 0o664)
    with pytest.raises(Exception) as e:
        load_mapping(p)
    assert e.value.reason == "untrusted_file"
