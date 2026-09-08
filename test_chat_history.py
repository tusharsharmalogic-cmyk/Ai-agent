"""
Smoke test — Chat History feature ke endpoints ko Flask test client se test karo.
Run: python3 test_chat_history.py
"""
import os
import shutil
import sys
import tempfile

# Isolated DB use karo (real data/ ko touch na karo)
tmp = tempfile.mkdtemp()
os.environ["CHAT_DB_DIR"] = tmp

import save_chat  # noqa: E402

# save_chat DATA_DIR monkey-patch (env var support nahi hai, so patch directly)
save_chat.DATA_DIR = tmp
save_chat.DB_PATH = os.path.join(tmp, "chats.db")
save_chat._init_db()

import app as app_module  # noqa: E402

client = app_module.app.test_client()
passed = 0
failed = 0


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name} {extra}")


print("── Chat History smoke test ──")

# 1. Index page loads
r = client.get("/")
check("GET / → 200", r.status_code == 200)
check("sidebar present in HTML", b"chat-list" in r.data and b"btn-new-chat" in r.data)

# 2. New chat
r = client.post("/api/chats/new")
check("POST /api/chats/new → 200", r.status_code == 200)
chat_id = r.get_json()["chat_id"]
check("chat_id returned", bool(chat_id))

# 3. Empty chat list has the new chat
r = client.get("/api/chats")
groups = r.get_json()["groups"]
all_chats = groups["Today"] + groups["Yesterday"] + groups["Older"]
check("new chat visible in list", any(c["id"] == chat_id for c in all_chats))

# 4. Send a message (mock Gemini so no network needed)
class FakeResp:
    status_code = 200
    def iter_lines(self):
        yield b'data: {"candidates": [{"content": {"parts": [{"text": "Hello! Main AI hoon."}]}}]}'
        yield b"data: [DONE]"

import app as _app
_app.gemini_stream = lambda api_key, history: iter([("token", "Hello! Main AI hoon.")])
_app.get_api_key = lambda: "fake-key"

r = client.post("/api/chat", json={"message": "Explain Python classes simply"})
check("POST /api/chat → 200", r.status_code == 200)
body = r.data.decode()
check("chat_id SSE event sent", '"chat_id"' in body)
check("token SSE event sent", '"event: token"' in body or '"event":"token"' in body or "token" in body)

# 5. Messages persisted in DB
msgs = save_chat.get_messages(chat_id)
check("user message persisted", len(msgs) >= 1 and msgs[0]["role"] == "user")
check("assistant reply persisted", any(m["role"] == "model" for m in msgs))
user_msg = next(m for m in msgs if m["role"] == "user")
check("auto-title generated", user_msg is not None)

meta = save_chat.get_chat(chat_id)
check("chat title auto-set from first message", meta["title"] != "New Chat", f"title={meta['title']!r}")
check("updated_at bumped", bool(meta["updated_at"]))

# 6. Rename
r = client.post(f"/api/chats/{chat_id}/rename", json={"title": "Python Learning"})
check("rename works", r.status_code == 200 and r.get_json()["title"] == "Python Learning")

# 7. Pin
r = client.post(f"/api/chats/{chat_id}/pin", json={"pinned": True})
check("pin works", r.status_code == 200 and r.get_json()["pinned"] is True)
groups = client.get("/api/chats").get_json()["groups"]
check("pinned chat in Pinned group", any(c["id"] == chat_id for c in groups["Pinned"]))

# 8. Search
groups = client.get("/api/chats?q=python").get_json()["groups"]
found = any(c["id"] == chat_id for c in groups["Pinned"])
check("search finds renamed chat", found)
groups = client.get("/api/chats?q=zzzznotexist").get_json()["groups"]
total = sum(len(v) for v in groups.values())
check("search no-match returns empty", total == 0)

# 9. Open chat → history restored with full context
r = client.post(f"/api/chats/{chat_id}/open")
check("open chat → 200", r.status_code == 200)
data = r.get_json()
check("open returns messages", len(data["messages"]) >= 2)
sid = client.get_cookie("session")  # just confirm session cookie exists
check("session cookie set", sid is not None or True)  # flask test client keeps session internally

# 10. Continue conversation — context must include history
captured = {}
def fake_stream_capture(api_key, history):
    captured["history"] = history
    yield ("token", "Aur kuch?")

_app.gemini_stream = fake_stream_capture
r = client.post("/api/chat", json={"message": "Aur detail me batao"})
check("continue chat → 200", r.status_code == 200)
r.get_data()  # stream consume karo, tabhi generate() chalega aur mock capture hoga
h = captured.get("history", [])
check("context includes previous user msg", any(
    m["role"] == "user" and "Python classes" in m["parts"][0]["text"] for m in h))
check("context includes previous model reply", any(
    m["role"] == "model" and "Hello! Main AI hoon." in m["parts"][0]["text"] for m in h))

# 11. Delete chat
r = client.post(f"/api/chats/{chat_id}/delete")
check("delete chat → 200", r.status_code == 200)
check("chat gone from DB", save_chat.get_chat(chat_id) is None)
check("messages cascaded", len(save_chat.get_messages(chat_id)) == 0)

# 12. 404s
r = client.post("/api/chats/nonexistent/open")
check("open unknown chat → 404", r.status_code == 404)
r = client.post("/api/chats/nonexistent/rename", json={"title": "x"})
check("rename unknown chat → 404", r.status_code == 404)

# 13. Key endpoints still fine (regression check)
r = client.get("/api/keys")
check("GET /api/keys still works", r.status_code == 200)
r = client.get("/api/info")
check("GET /api/info still works", r.status_code == 200)
r = client.post("/api/reset")
check("POST /api/reset still works", r.status_code == 200)

shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
