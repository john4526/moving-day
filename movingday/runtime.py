from dataclasses import dataclass

from movingday.api import Calendar, Directory, Gmail
from movingday.config import Config

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
    src_cal: Calendar
    dst_gmail: Gmail | None = None
    dst_cal: Calendar | None = None
    dst_dir: Directory | None = None


def get_apis(config: Config, source_only: bool = False) -> Apis:
    """source_only: no destination object is built, so the destination key file is never opened."""
    src = dict(src_gmail=Gmail(config.source_key_file, SRC_SCOPES),
               src_cal=Calendar(config.source_key_file, SRC_SCOPES))
    if source_only:
        return Apis(**src)
    return Apis(
        **src,
        dst_gmail=Gmail(config.dest_key_file, DST_SCOPES),
        dst_cal=Calendar(config.dest_key_file, DST_SCOPES),
        dst_dir=Directory(config.dest_key_file, config.dest_admin),
    )
