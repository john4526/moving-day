import pytest

from wsmigrate.config import load_config

BASE = '''
source_key_file = "s.json"
dest_key_file = "d.json"
mapping_file = "m.csv"
inventory_file = "i.json"
ledger_path = "l.sqlite"
report_dir = "%s"
dest_admin = "admin@new.org"
dest_domain = "tmp.new.org"
'''


def write(tmp_path, report_dir="reports", extra=""):
    p = tmp_path / "c.toml"
    p.write_text(BASE % report_dir + extra)
    return str(p)


def test_loads_and_applies_defaults(tmp_path):
    c = load_config(write(tmp_path))
    assert c.dest_admin == "admin@new.org"
    assert c.parallel_users == 4
    assert c.max_existing == 5
    assert load_config(write(tmp_path, extra="parallel_users = 2\n")).parallel_users == 2


def test_report_dir_with_space_rejected(tmp_path):
    with pytest.raises(ValueError):
        load_config(write(tmp_path, report_dir="my reports"))
