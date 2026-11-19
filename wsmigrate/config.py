import tomllib
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    source_key_file: str
    dest_key_file: str
    mapping_file: str
    inventory_file: str
    ledger_path: str
    report_dir: str  # must contain no spaces
    dest_admin: str  # admin address used for Directory calls
    dest_domain: str  # the temporary domain
    parallel_users: int = 4
    max_existing: int = 5


def load_config(path: str) -> Config:
    with open(path, "rb") as f:
        cfg = Config(**tomllib.load(f))
    if any(c.isspace() for c in cfg.report_dir):
        raise ValueError("report_dir must not contain spaces")
    return cfg
