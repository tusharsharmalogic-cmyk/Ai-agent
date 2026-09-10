"""
save_chat.py — JSON file persistence layer for Chat History.

Storage: data/chats/<chat_id>.json (har chat ka alag JSON file)
Index: data/index.json (saare chats ki metadata + pinned status)

Har chat file ka format:
{
    "id": "...",
    "title": "...",
    "created_at": "...",
    "updated_at": "...",
    "pinned": false,
    "messages": [
        {"role": "user", "content": "...", "timestamp": "..."},
        {"role": "model", "content": "...", "timestamp": "..."}
    ]
}
"""
import json
import os
import threading
import uuid
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CHATS_DIR = os.path.join(DATA_DIR, "chats")
INDEX_FILE = os.path.join(DATA_DIR, "index.json")

# Thread-safe file operations ke liye lock
_LOCK = threading.RLock()  # Re-entrant lock — nested with blocks support


def _ensure_dirs():
    """Data directories create karo agar exist nahi karte."""
    os.makedirs(CHATS_DIR, exist_ok=True)


def _now():
    """Current timestamp ISO format me."""
    return datetime.now().isoformat(timespec="seconds")


def _chat_file(chat_id):
    """Chat file ka path return karo."""
    return os.path.join(CHATS_DIR, f"{chat_id}.json")


def _load_index():
    """Index file load karo (saare chats ki metadata)."""
    if os.path.exists(INDEX_FILE):
        try:
            with open(INDEX_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def _save_index(index):
    """Index file save karo."""
    with open(INDEX_FILE, "w") as f:
        json.dump(index, f, indent=2)


def _load_chat(chat_id):
    """Single chat file load karo."""
    _ensure_dirs()
    filepath = _chat_file(chat_id)
    if os.path.exists(filepath):
        try:
            with open(filepath) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None
    return None


def _save_chat(chat_data):
    """Single chat file save karo."""
    _ensure_dirs()
    filepath = _chat_file(chat_data["id"])
    with open(filepath, "w") as f:
        json.dump(chat_data, f, indent=2)


def _update_index(chat_id, title=None, updated_at=None, pinned=None):
    """Index me chat metadata update karo."""
    with _LOCK:
        index = _load_index()
        if chat_id not in index:
            return
        if title is not None:
            index[chat_id]["title"] = title
        if updated_at is not None:
            index[chat_id]["updated_at"] = updated_at
        if pinned is not None:
            index[chat_id]["pinned"] = pinned
        _save_index(index)


# ─── Chat CRUD ────────────────────────────────────────────────────────────

def create_chat(chat_id=None, title="New Chat"):
    """Naya chat banao (empty message list)."""
    _ensure_dirs()
    chat_id = chat_id or uuid.uuid4().hex
    now = _now()

    chat_data = {
        "id": chat_id,
        "title": title,
        "created_at": now,
        "updated_at": now,
        "pinned": False,
        "messages": []
    }

    with _LOCK:
        _save_chat(chat_data)
        index = _load_index()
        index[chat_id] = {
            "id": chat_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "pinned": False
        }
        _save_index(index)

    return chat_id


def list_chats(search=None):
    """Saare chats latest activity order me (pinned pehle), search optional."""
    with _LOCK:
        index = _load_index()

    chats = list(index.values())

    # Search filter
    if search:
        search_lower = search.lower()
        chats = [c for c in chats if search_lower in c["title"].lower()]

    # Sort: pinned pehle (True > False), phir updated_at descending
    chats.sort(key=lambda c: (c["pinned"], c["updated_at"]), reverse=True)

    # Group by date
    grouped = {"Pinned": [], "Today": [], "Yesterday": [], "Older": []}
    for chat in chats:
        chat_copy = dict(chat)
        chat_copy["pinned"] = bool(chat_copy["pinned"])
        chat_copy["message_count"] = _get_message_count(chat_copy["id"])
        if chat_copy["pinned"]:
            chat_copy["group"] = "Pinned"
            grouped["Pinned"].append(chat_copy)
        else:
            chat_copy["group"] = _date_group(chat_copy["updated_at"])
            grouped[chat_copy["group"]].append(chat_copy)

    return grouped


def _get_message_count(chat_id):
    """Chat ke messages ki count return karo."""
    chat = _load_chat(chat_id)
    if chat:
        return len(chat.get("messages", []))
    return 0


def _date_group(iso_ts):
    """ISO timestamp ko Today / Yesterday / Older group me daalo."""
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
    chat = _load_chat(chat_id)
    if not chat:
        return None
    return {
        "id": chat["id"],
        "title": chat["title"],
        "created_at": chat["created_at"],
        "updated_at": chat["updated_at"],
        "pinned": bool(chat["pinned"])
    }


def rename_chat(chat_id, title):
    """Chat ka title rename karo."""
    with _LOCK:
        chat = _load_chat(chat_id)
        if not chat:
            return False
        chat["title"] = title
        chat["updated_at"] = _now()
        _save_chat(chat)
        _update_index(chat_id, title=title, updated_at=chat["updated_at"])
    return True


def set_pinned(chat_id, pinned):
    """Chat ko pinned/unpinned karo."""
    with _LOCK:
        chat = _load_chat(chat_id)
        if not chat:
            return False
        chat["pinned"] = bool(pinned)
        chat["updated_at"] = _now()
        _save_chat(chat)
        _update_index(chat_id, updated_at=chat["updated_at"], pinned=bool(pinned))
    return True


def delete_chat(chat_id):
    """Chat file + index entry delete karo."""
    with _LOCK:
        filepath = _chat_file(chat_id)
        if os.path.exists(filepath):
            os.remove(filepath)
        index = _load_index()
        if chat_id in index:
            del index[chat_id]
            _save_index(index)
            return True
    return False


# ─── Message persistence ─────────────────────────────────────────────────

def add_message(chat_id, role, content):
    """
    Chat file me message append karo.
    Returns (message_id, timestamp).
    """
    from datetime import datetime

    now = _now()
    with _LOCK:
        chat = _load_chat(chat_id)
        if not chat:
            return None, None

        # Message ID: last message ka ID + 1 (ya 1 agar pehla message)
        messages = chat.get("messages", [])
        msg_id = len(messages) + 1

        message = {
            "id": msg_id,
            "role": role,
            "content": content,
            "timestamp": now
        }
        messages.append(message)
        chat["messages"] = messages
        chat["updated_at"] = now
        _save_chat(chat)
        _update_index(chat_id, updated_at=now)

    return msg_id, now


def set_title_if_new(chat_id, title):
    """Title sirf tab set karo jab chat abhi bhi default 'New Chat' ho (auto-title)."""
    with _LOCK:
        chat = _load_chat(chat_id)
        if not chat:
            return False
        if chat["title"] == "New Chat":
            chat["title"] = title
            chat["updated_at"] = _now()
            _save_chat(chat)
            _update_index(chat_id, title=title, updated_at=chat["updated_at"])
            return True
    return False


def get_messages(chat_id):
    """Chat ke saare messages, ordered by id (insertion order = conversation order)."""
    chat = _load_chat(chat_id)
    if not chat:
        return []
    return chat.get("messages", [])


def touch_chat(chat_id):
    """Chat ka updated_at refresh karo."""
    now = _now()
    with _LOCK:
        chat = _load_chat(chat_id)
        if not chat:
            return
        chat["updated_at"] = now
        _save_chat(chat)
        _update_index(chat_id, updated_at=now)


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
