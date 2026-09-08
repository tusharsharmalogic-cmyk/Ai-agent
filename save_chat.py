"""
save_chat.py — SQLite persistence layer for Chat History.

DB: data/chats.db (auto-created on first import).
Tables: chats (id, title, created_at, updated_at, pinned)
        messages (id, chat_id, role, content, timestamp)

Sab queries parameterized hain — kabhi string concatenation nahi.
"""
import os
import sqlite3
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "chats.db")

# Flask threaded=True mode me chalta hai — har request ke liye naya
# connection + check_same_thread=False with a lock is safest for SQLite.
_LOCK = threading.Lock()


def _connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = _connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chats (
                id          TEXT PRIMARY KEY,
                title       TEXT NOT NULL DEFAULT 'New Chat',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                pinned      INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id    TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                role       TEXT NOT NULL CHECK (role IN ('user', 'model')),
                content    TEXT NOT NULL,
                timestamp  TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages(chat_id);
            CREATE INDEX IF NOT EXISTS idx_chats_updated_at ON chats(updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_chats_title      ON chats(title);
            """
        )
        conn.commit()
    finally:
        conn.close()


_init_db()


# ─── Chat CRUD ────────────────────────────────────────────────────────────

def create_chat(chat_id=None, title="New Chat"):
    """Naya chat record banao (empty message list)."""
    import uuid
    from datetime import datetime

    chat_id = chat_id or uuid.uuid4().hex
    now = datetime.now().isoformat(timespec="seconds")
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO chats (id, title, created_at, updated_at, pinned) "
                "VALUES (?, ?, ?, ?, 0)",
                (chat_id, title, now, now),
            )
            conn.commit()
        finally:
            conn.close()
    return chat_id


def list_chats(search=None):
    """Saare chats latest activity order me (pinned pehle), search optional."""
    with _LOCK:
        conn = _connect()
        try:
            sql = (
                "SELECT c.id, c.title, c.created_at, c.updated_at, c.pinned, "
                "       (SELECT COUNT(*) FROM messages m WHERE m.chat_id = c.id) AS message_count "
                "FROM chats c "
            )
            params = []
            if search:
                sql += "WHERE c.title LIKE ? ESCAPE '\\' COLLATE NOCASE "
                params.append("%" + _escape_like(search) + "%")
            sql += (
                "ORDER BY c.pinned DESC, c.updated_at DESC "
            )
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    grouped = {"Pinned": [], "Today": [], "Yesterday": [], "Older": []}
    for r in rows:
        chat = dict(r)
        chat["pinned"] = bool(chat["pinned"])
        if chat["pinned"]:
            chat["group"] = "Pinned"
            grouped["Pinned"].append(chat)
        else:
            chat["group"] = _date_group(chat["updated_at"])
            grouped[chat["group"]].append(chat)
    return grouped


def _escape_like(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _date_group(iso_ts):
    """ISO timestamp ko Today / Yesterday / Older group me daalo."""
    from datetime import datetime, timedelta

    try:
        dt = datetime.fromisoformat(iso_ts)
    except (TypeError, ValueError):
        return "Older"
    today = datetime.now().date()
    d = dt.date()
    if d == today:
        return "Today"
    if d == today - timedelta(days=1):
        return "Yesterday"
    return "Older"


def get_chat(chat_id):
    """Single chat metadata (ya None)."""
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT id, title, created_at, updated_at, pinned FROM chats WHERE id = ?",
                (chat_id,),
            ).fetchone()
        finally:
            conn.close()
    if not row:
        return None
    chat = dict(row)
    chat["pinned"] = bool(chat["pinned"])
    return chat


def rename_chat(chat_id, title):
    with _LOCK:
        conn = _connect()
        try:
            cur = conn.execute(
                "UPDATE chats SET title = ? WHERE id = ?", (title, chat_id)
            )
            conn.commit()
            ok = cur.rowcount > 0
        finally:
            conn.close()
    return ok


def set_pinned(chat_id, pinned):
    with _LOCK:
        conn = _connect()
        try:
            cur = conn.execute(
                "UPDATE chats SET pinned = ? WHERE id = ?", (1 if pinned else 0, chat_id)
            )
            conn.commit()
            ok = cur.rowcount > 0
        finally:
            conn.close()
    return ok


def delete_chat(chat_id):
    """Chat + uske messages delete (messages FK ON DELETE CASCADE)."""
    with _LOCK:
        conn = _connect()
        try:
            cur = conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
            conn.commit()
            ok = cur.rowcount > 0
        finally:
            conn.close()
    return ok


# ─── Message persistence ─────────────────────────────────────────────────

def add_message(chat_id, role, content):
    """
    Ek message INSERT karo (incremental — poori history kabhi overwrite nahi hoti).
    chats.updated_at bhi bump hota hai.
    Returns (message_id, timestamp).
    """
    from datetime import datetime

    now = datetime.now().isoformat(timespec="seconds")
    with _LOCK:
        conn = _connect()
        try:
            cur = conn.execute(
                "INSERT INTO messages (chat_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                (chat_id, role, content, now),
            )
            conn.execute(
                "UPDATE chats SET updated_at = ? WHERE id = ?", (now, chat_id)
            )
            conn.commit()
            msg_id = cur.lastrowid
        finally:
            conn.close()
    return msg_id, now


def set_title_if_new(chat_id, title):
    """Title sirf tab set karo jab chat abhi bhi default 'New Chat' ho (auto-title)."""
    with _LOCK:
        conn = _connect()
        try:
            cur = conn.execute(
                "UPDATE chats SET title = ? WHERE id = ? AND title = 'New Chat'",
                (title, chat_id),
            )
            conn.commit()
            ok = cur.rowcount > 0
        finally:
            conn.close()
    return ok


def get_messages(chat_id):
    """Chat ke saare messages, ordered by id (insertion order = conversation order)."""
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT id, role, content, timestamp FROM messages WHERE chat_id = ? ORDER BY id ASC",
                (chat_id,),
            ).fetchall()
        finally:
            conn.close()
    return [dict(r) for r in rows]


def touch_chat(chat_id):
    from datetime import datetime
    now = datetime.now().isoformat(timespec="seconds")
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "UPDATE chats SET updated_at = ? WHERE id = ?", (now, chat_id)
            )
            conn.commit()
        finally:
            conn.close()


# ─── Auto titles ─────────────────────────────────────────────────────────

def auto_title(message, max_len=48):
    """
    Pehle user message se simple title banao.
    Hinglish/English dono me kaam karta hai.
    """
    text = " ".join((message or "").split())
    if not text:
        return "New Chat"

    # Boilerplate strip
    for prefix in ("please ", "can you ", "could you ", "mujhe ", "bhai ",
                   "plz ", "pls ", "kindly "):
        if text.lower().startswith(prefix):
            text = text[len(prefix):]

    # Pehla meaningful sentence fragment lo
    for sep in ("?", "!", ".", "\n"):
        if sep in text:
            first = text.split(sep)[0]
            if len(first.strip()) >= 8:
                text = first
                break

    text = " ".join(text.split())
    if len(text) <= max_len:
        return text or "New Chat"
    return text[: max_len - 1].rstrip() + "…"
