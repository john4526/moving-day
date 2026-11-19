"""SQLite progress ledger. Stores ids, hashes, states and reason codes only; never content."""
import sqlite3
import threading

# Journal-mode change and schema creation on a fresh file can return BUSY without
# honouring busy_timeout when several connections race; serialise setup in-process.
_SETUP = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages(
  user_key TEXT NOT NULL, src_id TEXT NOT NULL, src_thread TEXT NOT NULL DEFAULT '',
  dst_id TEXT NOT NULL DEFAULT '', dst_thread TEXT NOT NULL DEFAULT '',
  msgid_hash TEXT NOT NULL DEFAULT '', state TEXT NOT NULL, reason TEXT,
  PRIMARY KEY(user_key, src_id));
CREATE INDEX IF NOT EXISTS messages_hash ON messages(user_key, msgid_hash);
CREATE INDEX IF NOT EXISTS messages_thread ON messages(user_key, src_thread);
CREATE TABLE IF NOT EXISTS calendars(
  user_key TEXT NOT NULL, src_cal TEXT NOT NULL, dst_cal TEXT NOT NULL,
  PRIMARY KEY(user_key, src_cal));
CREATE TABLE IF NOT EXISTS events(
  user_key TEXT NOT NULL, src_cal TEXT NOT NULL, src_event TEXT NOT NULL,
  dst_event TEXT NOT NULL, updated TEXT NOT NULL, state TEXT NOT NULL, reason TEXT,
  PRIMARY KEY(user_key, src_cal, src_event));
CREATE TABLE IF NOT EXISTS meta(
  user_key TEXT NOT NULL, name TEXT NOT NULL, value TEXT NOT NULL,
  PRIMARY KEY(user_key, name));
"""


class Ledger:
    """One instance per thread; many instances may share one file (WAL)."""

    def __init__(self, path: str):
        self.db = sqlite3.connect(path, timeout=30)
        self.db.execute("PRAGMA busy_timeout=30000")
        with _SETUP:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.executescript(_SCHEMA)
            self.db.commit()

    def close(self) -> None:
        self.db.close()

    def _w(self, sql, args=()):
        with self.db:  # commits after every write
            self.db.execute(sql, args)

    def _r(self, sql, args=()):
        return self.db.execute(sql, args).fetchall()

    # mail
    def done_ids(self, user_key):
        return {r[0] for r in self._r(
            "SELECT src_id FROM messages WHERE user_key=? AND state='done'", (user_key,))}

    def mark_pending(self, user_key, src_id, src_thread, msgid_hash):
        # failed -> pending (retry); done stays done
        self._w(
            "INSERT INTO messages(user_key,src_id,src_thread,msgid_hash,state) VALUES(?,?,?,?,'pending') "
            "ON CONFLICT(user_key,src_id) DO UPDATE SET state='pending', reason=NULL, "
            "src_thread=excluded.src_thread, msgid_hash=excluded.msgid_hash "
            "WHERE messages.state!='done'",
            (user_key, src_id, src_thread, msgid_hash))

    def mark_done(self, user_key, src_id, dst_id, dst_thread):
        self._w(
            "INSERT INTO messages(user_key,src_id,dst_id,dst_thread,state) VALUES(?,?,?,?,'done') "
            "ON CONFLICT(user_key,src_id) DO UPDATE SET state='done', reason=NULL, "
            "dst_id=excluded.dst_id, dst_thread=excluded.dst_thread",
            (user_key, src_id, dst_id, dst_thread))

    def mark_failed(self, user_key, src_id, reason):
        self._w(
            "INSERT INTO messages(user_key,src_id,state,reason) VALUES(?,?,'failed',?) "
            "ON CONFLICT(user_key,src_id) DO UPDATE SET state='failed', reason=excluded.reason "
            "WHERE messages.state!='done'",
            (user_key, src_id, reason))

    def pending(self, user_key):
        return [(r[0], r[1]) for r in self._r(
            "SELECT src_id, msgid_hash FROM messages WHERE user_key=? AND state='pending' ORDER BY src_id",
            (user_key,))]

    def drop_pending(self, user_key, src_id):
        self._w("DELETE FROM messages WHERE user_key=? AND src_id=? AND state='pending'",
                (user_key, src_id))

    def done_hash_count(self, user_key, msgid_hash):
        return self._r(
            "SELECT COUNT(*) FROM messages WHERE user_key=? AND msgid_hash=? AND state='done'",
            (user_key, msgid_hash))[0][0]

    def dst_thread(self, user_key, src_thread):
        r = self._r(
            "SELECT dst_thread FROM messages WHERE user_key=? AND src_thread=? AND state='done' "
            "AND dst_thread!='' LIMIT 1", (user_key, src_thread))
        return r[0][0] if r else None

    def counts(self, user_key):
        out = {"done": 0, "failed": 0, "pending": 0}
        out.update(dict(self._r(
            "SELECT state, COUNT(*) FROM messages WHERE user_key=? GROUP BY state", (user_key,))))
        return out

    def failures(self, user_key):
        out: dict[str, int] = {}
        for table in ("messages", "events"):
            for reason, n in self._r(
                    f"SELECT COALESCE(reason,'unknown'), COUNT(*) FROM {table} "
                    "WHERE user_key=? AND state='failed' GROUP BY 1", (user_key,)):
                out[reason] = out.get(reason, 0) + n
        return out

    # calendar
    def cal_map(self, user_key):
        return dict(self._r("SELECT src_cal, dst_cal FROM calendars WHERE user_key=?", (user_key,)))

    def set_cal(self, user_key, src_cal, dst_cal):
        self._w("INSERT OR REPLACE INTO calendars VALUES(?,?,?)", (user_key, src_cal, dst_cal))

    def event_rows(self, user_key, src_cal):
        return {r[0]: (r[1], r[2], r[3]) for r in self._r(
            "SELECT src_event, dst_event, updated, state FROM events WHERE user_key=? AND src_cal=?",
            (user_key, src_cal))}

    def failed_event_ids(self, user_key, src_cal, reason):
        return {r[0] for r in self._r(
            "SELECT src_event FROM events WHERE user_key=? AND src_cal=? AND state='failed' AND reason=?",
            (user_key, src_cal, reason))}

    def set_event(self, user_key, src_cal, src_event, dst_event, updated, state, reason=None):
        self._w("INSERT OR REPLACE INTO events VALUES(?,?,?,?,?,?,?)",
                (user_key, src_cal, src_event, dst_event, updated, state, reason))

    def drop_event(self, user_key, src_cal, src_event):
        self._w("DELETE FROM events WHERE user_key=? AND src_cal=? AND src_event=?",
                (user_key, src_cal, src_event))

    # per-user facts
    def set_meta(self, user_key, name, value):
        self._w("INSERT OR REPLACE INTO meta VALUES(?,?,?)", (user_key, name, value))

    def get_meta(self, user_key, name):
        r = self._r("SELECT value FROM meta WHERE user_key=? AND name=?", (user_key, name))
        return r[0][0] if r else None
