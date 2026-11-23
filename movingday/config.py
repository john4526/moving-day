import os
import tomllib
from dataclasses import dataclass


class UntrustedFile(Exception):
    reason = "untrusted_file"


if os.name == "nt":
    import msvcrt

    import ntsecuritycon as con
    import win32api
    import win32security as ws

    # Any of these lets a principal change or replace the file.
    _WRITE = (con.FILE_WRITE_DATA | con.FILE_APPEND_DATA | con.WRITE_DAC | con.WRITE_OWNER
              | con.DELETE | con.GENERIC_WRITE | con.GENERIC_ALL)

    def trusted_sids() -> list:
        """This user, plus SYSTEM and Administrators, who can change any file on the machine anyway."""
        token = ws.OpenProcessToken(win32api.GetCurrentProcess(), con.TOKEN_QUERY)
        return [ws.GetTokenInformation(token, ws.TokenUser)[0],
                ws.CreateWellKnownSid(ws.WinLocalSystemSid),
                ws.CreateWellKnownSid(ws.WinBuiltinAdministratorsSid)]

    def require_trusted(f) -> None:
        """The agent may pass any --config: only trust files owned by this user and writable by no one else."""
        sd = ws.GetSecurityInfo(msvcrt.get_osfhandle(f.fileno()), ws.SE_FILE_OBJECT,
                                ws.OWNER_SECURITY_INFORMATION | ws.DACL_SECURITY_INFORMATION)
        trusted, dacl = trusted_sids(), sd.GetSecurityDescriptorDacl()
        if sd.GetSecurityDescriptorOwner() not in trusted or dacl is None:  # no DACL = everyone has full access
            raise UntrustedFile()
        for i in range(dacl.GetAceCount()):
            (ace_type, flags), mask, sid = dacl.GetAce(i)
            if (ace_type == con.ACCESS_ALLOWED_ACE_TYPE and not flags & con.INHERIT_ONLY_ACE
                    and mask & _WRITE and sid not in trusted):
                raise UntrustedFile()
else:
    def require_trusted(f) -> None:
        """The agent may pass any --config: only trust files owned by this user and writable by no one else."""
        st = os.fstat(f.fileno())
        if st.st_uid != os.geteuid() or st.st_mode & 0o022:
            raise UntrustedFile()


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
        require_trusted(f)
        cfg = Config(**tomllib.load(f))
    if any(c.isspace() for c in cfg.report_dir):
        raise ValueError("report_dir must not contain spaces")
    return cfg
