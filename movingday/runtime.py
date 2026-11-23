from dataclasses import dataclass

from movingday.api import Calendar, Directory, Drive, Gmail, People
from movingday.config import Config

_G = "https://www.googleapis.com/auth/"
SRC_SCOPES = [_G + "gmail.readonly", _G + "calendar.readonly", _G + "calendar.acls.readonly",
              _G + "contacts.readonly", _G + "contacts.other.readonly", _G + "drive.readonly"]
DST_SCOPES = [
    _G + "gmail.insert",
    _G + "gmail.labels",
    _G + "gmail.metadata",
    _G + "gmail.modify",  # messages.modify: labels of an adopted earlier copy (--allow-existing)
    _G + "calendar",
    _G + "contacts",
    _G + "drive.file",
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
    src_people: People | None = None  # always set by get_apis; defaulted only for field order
    dst_people: People | None = None
    src_drive: Drive | None = None
    dst_drive: Drive | None = None


def get_apis(config: Config, source_only: bool = False) -> Apis:
    """source_only: no destination object is built, so the destination key file is never opened."""
    src = dict(src_gmail=Gmail(config.source_key_file, SRC_SCOPES),
               src_cal=Calendar(config.source_key_file, SRC_SCOPES),
               src_people=People(config.source_key_file, SRC_SCOPES),
               src_drive=Drive(config.source_key_file, SRC_SCOPES))
    if source_only:
        return Apis(**src)
    return Apis(
        **src,
        dst_gmail=Gmail(config.dest_key_file, DST_SCOPES),
        dst_cal=Calendar(config.dest_key_file, DST_SCOPES),
        dst_dir=Directory(config.dest_key_file, config.dest_admin),
        dst_people=People(config.dest_key_file, DST_SCOPES),
        dst_drive=Drive(config.dest_key_file, DST_SCOPES),
    )
