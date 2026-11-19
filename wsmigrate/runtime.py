from dataclasses import dataclass

from wsmigrate.api import Calendar, Directory, Gmail
from wsmigrate.config import Config

_G = "https://www.googleapis.com/auth/"
SRC_SCOPES = [_G + "gmail.readonly", _G + "calendar.readonly", _G + "calendar.acls.readonly"]
DST_SCOPES = [
    _G + "gmail.insert",
    _G + "gmail.labels",
    _G + "gmail.metadata",
    _G + "calendar",
    _G + "admin.directory.user.readonly",
    _G + "admin.directory.group.readonly",
]


@dataclass
class Apis:
    src_gmail: Gmail
    dst_gmail: Gmail
    src_cal: Calendar
    dst_cal: Calendar
    dst_dir: Directory


def get_apis(config: Config) -> Apis:
    return Apis(
        src_gmail=Gmail(config.source_key_file, SRC_SCOPES),
        dst_gmail=Gmail(config.dest_key_file, DST_SCOPES),
        src_cal=Calendar(config.source_key_file, SRC_SCOPES),
        dst_cal=Calendar(config.dest_key_file, DST_SCOPES),
        dst_dir=Directory(config.dest_key_file, config.dest_admin),
    )
