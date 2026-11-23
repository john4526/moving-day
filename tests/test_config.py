import os

import pytest

from fakes import make_writable_by_others
from movingday import config
from movingday.config import load_config

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


@pytest.mark.parametrize("mode", [0o600, 0o644])
def test_config_owned_by_user_and_not_writable_by_others_accepted(tmp_path, mode):
    p = write(tmp_path)
    os.chmod(p, mode)
    assert load_config(p).dest_domain == "tmp.new.org"


@pytest.mark.parametrize("mode", [0o664, 0o646])
def test_config_writable_by_group_or_others_refused(tmp_path, mode):
    p = write(tmp_path)
    make_writable_by_others(p, mode)
    with pytest.raises(Exception) as e:
        load_config(p)
    assert e.value.reason == "untrusted_file"


def test_config_owned_by_someone_else_refused(tmp_path, monkeypatch):
    p = write(tmp_path)
    if os.name == "nt":
        import win32security as ws
        monkeypatch.setattr(config, "trusted_sids", lambda: [ws.CreateWellKnownSid(ws.WinWorldSid)])
    else:
        monkeypatch.setattr(os, "geteuid", lambda: os.stat(p).st_uid + 1)
    with pytest.raises(Exception) as e:
        load_config(p)
    assert e.value.reason == "untrusted_file"
