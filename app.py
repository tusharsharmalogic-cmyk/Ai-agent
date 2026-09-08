"""
AI Agent Web — Flask + Gemini streaming chat with auto command execution.
CLI twin of chat.py: same system prompt, same RUN_CMD loop, same blacklist.

Run:  python3 app.py   (binds 0.0.0.0, uses $PORT if set)
"""
import json
import os
import time
import uuid

import requests
from flask import (
    Flask,
    Response,
    jsonify,
    render_template,
    request,
    session,
    stream_with_context,
)

import save_chat
from chat import (
    SYSTEM_PROMPT,
    add_key,
    build_followup_message,
    extract_and_run_commands,
    get_active_index,
    get_all_keys,
    get_api_key_env,
    load_api_key,
    mask_key,
    remove_key,
    save_api_key,
    switch_key,
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "ai-agent-web-dev-secret")

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse"
MAX_HISTORY = 30  # last N messages sent to the API (context trim)

# browser session id -> chat history (in-memory)
SESSIONS = {}

# current chat id per browser session (chat history persistence)
CHAT_IDS = {}


# ─── Helpers ──────────────────────────────────────────────────────────────

def get_sid():
    if "sid" not in session:
        session["sid"] = uuid.uuid4().hex
    return session["sid"]


def get_history():
    return SESSIONS.setdefault(get_sid(), [])


def get_chat_id():
    """Is browser session ka current chat id (DB-backed)."""
    return CHAT_IDS.get(get_sid())


def get_api_key():
    return get_api_key_env() or load_api_key()


def sse(event, data):
    return "event: %s\ndata: %s\n\n" % (event, json.dumps(data))


def gemini_stream(api_key, history):
    """Gemini SSE ko parse karke ('token', text) / ('error', msg) / ('retry', msg) yield karo."""
    payload = {
        "system_instruction": {"role": "system", "parts": [{"text": SYSTEM_PROMPT}]},
        "contents": history[-MAX_HISTORY:],
    }

    def call():
        return requests.post(
            API_URL.format(model=MODEL),
            params={"key": api_key},
            json=payload,
            stream=True,
            timeout=(10, 120),
        )

    # 429 aur 503 dono ke liye exponential backoff retry
    max_retries = 4
    response = None
    for attempt in range(max_retries):
        response = call()
        if response.status_code == 429:
            wait = 5 * (2 ** attempt)  # 5s, 10s, 20s, 40s
            yield ("retry", f"Rate limit — {wait}s baad retry ({attempt+1}/{max_retries})...")
            time.sleep(wait)
            continue
        if response.status_code == 503:
            yield ("retry", f"Server busy — 5s baad retry ({attempt+1}/{max_retries})...")
            time.sleep(5)
            continue
        break

    if response.status_code != 200:
        try:
            msg = response.json().get("error", {}).get("message", "")
        except Exception:
            msg = response.text[:300]
        yield ("error", "API Error %s: %s" % (response.status_code, msg))
        return

    for line in response.iter_lines():
        if line and line.startswith(b"data: "):
            try:
                data = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            if "error" in data:
                yield ("error", data["error"].get("message", "API error"))
                return
            for candidate in data.get("candidates", []):
                for part in candidate.get("content", {}).get("parts", []):
                    text = part.get("text", "")
                    if text:
                        yield ("token", text)


# ─── Routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/info")
def info():
    keys = get_all_keys()
    return jsonify(model=MODEL, configured=bool(keys), key_count=len(keys))


@app.route("/api/key", methods=["POST"])
def key_save():
    """Legacy single-key endpoint — naye multi-key format me save karo."""
    data = request.get_json(silent=True) or {}
    key = (data.get("key") or "").strip()
    if not key:
        return jsonify(error="API key required"), 400
    save_api_key(key)
    return jsonify(configured=True)


# ─── Multi-Key Management ───────────────────────────────────────────────

@app.route("/api/keys")
def keys_list():
    """Saari keys list karo (masked) + active index."""
    keys = get_all_keys()
    active = get_active_index()
    masked = [{"index": i, "masked": mask_key(k), "active": i == active}
              for i, k in enumerate(keys)]
    return jsonify(keys=masked, active_index=active)


@app.route("/api/keys/add", methods=["POST"])
def keys_add():
    """Nayi key add karo."""
    data = request.get_json(silent=True) or {}
    key = (data.get("key") or "").strip()
    if not key:
        return jsonify(error="API key required"), 400
    ok, msg = add_key(key)
    if not ok:
        return jsonify(error=msg), 409
    return jsonify(ok=True, message=msg)


@app.route("/api/keys/remove", methods=["POST"])
def keys_remove():
    """Key delete karo by index."""
    data = request.get_json(silent=True) or {}
    index = data.get("index")
    if index is None or not isinstance(index, int):
        return jsonify(error="index required"), 400
    ok, msg = remove_key(index)
    if not ok:
        return jsonify(error=msg), 400
    return jsonify(ok=True, message=msg)


@app.route("/api/keys/switch", methods=["POST"])
def keys_switch():
    """Active key change karo."""
    data = request.get_json(silent=True) or {}
    index = data.get("index")
    if index is None or not isinstance(index, int):
        return jsonify(error="index required"), 400
    ok, msg = switch_key(index)
    if not ok:
        return jsonify(error=msg), 400
    return jsonify(ok=True, message=msg)


@app.route("/api/reset", methods=["POST"])
def reset():
    SESSIONS.pop(get_sid(), None)
    CHAT_IDS.pop(get_sid(), None)
    return jsonify(ok=True)


@app.route("/api/history", methods=["GET"])
def history():
    visible = [
        m for m in get_history()
        if not (m["role"] == "user" and m["parts"][0]["text"].startswith("[System:"))
    ]
    return jsonify(messages=visible)


# ─── Chat History (SQLite persistence) ────────────────────────────────────

@app.route("/api/chats/new", methods=["POST"])
def chats_new():
    """Naya empty chat banao aur usse current session ka chat bana do."""
    SESSIONS.pop(get_sid(), None)
    chat_id = save_chat.create_chat()
    CHAT_IDS[get_sid()] = chat_id
    return jsonify(chat_id=chat_id, chat=save_chat.get_chat(chat_id))


@app.route("/api/chats")
def chats_list():
    """Saare chats grouped by date (Today/Yesterday/Older), search support."""
    search = (request.args.get("q") or "").strip() or None
    return jsonify(groups=save_chat.list_chats(search))


@app.route("/api/chats/<chat_id>")
def chats_get(chat_id):
    """Chat metadata + saare messages (old chat continue karne ke liye)."""
    meta = save_chat.get_chat(chat_id)
    if not meta:
        return jsonify(error="Chat nahi mila"), 404
    return jsonify(chat=meta, messages=save_chat.get_messages(chat_id))


@app.route("/api/chats/<chat_id>/open", methods=["POST"])
def chats_open(chat_id):
    """Purane chat ko current session me load karo (context ke saath)."""
    meta = save_chat.get_chat(chat_id)
    if not meta:
        return jsonify(error="Chat nahi mila"), 404

    history = []
    for m in save_chat.get_messages(chat_id):
        history.append({"role": m["role"], "parts": [{"text": m["content"]}]})
    SESSIONS[get_sid()] = history
    CHAT_IDS[get_sid()] = chat_id
    return jsonify(chat=meta, messages=save_chat.get_messages(chat_id))


@app.route("/api/chats/<chat_id>/rename", methods=["POST"])
def chats_rename(chat_id):
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify(error="Title required"), 400
    if len(title) > 120:
        title = title[:120]
    ok = save_chat.rename_chat(chat_id, title)
    if not ok:
        return jsonify(error="Chat nahi mila"), 404
    return jsonify(ok=True, title=title)


@app.route("/api/chats/<chat_id>/pin", methods=["POST"])
def chats_pin(chat_id):
    data = request.get_json(silent=True) or {}
    ok = save_chat.set_pinned(chat_id, bool(data.get("pinned", True)))
    if not ok:
        return jsonify(error="Chat nahi mila"), 404
    return jsonify(ok=True, pinned=bool(data.get("pinned", True)))


@app.route("/api/chats/<chat_id>/delete", methods=["POST"])
def chats_delete(chat_id):
    ok = save_chat.delete_chat(chat_id)
    if not ok:
        return jsonify(error="Chat nahi mila"), 404
    # Agar current chat delete hua to session reset
    if get_chat_id() == chat_id:
        SESSIONS.pop(get_sid(), None)
        CHAT_IDS.pop(get_sid(), None)
    return jsonify(ok=True)


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify(error="Empty message"), 400
    api_key = get_api_key()
    if not api_key:
        return jsonify(error="No API key configured"), 400

    history = get_history()
    history.append({"role": "user", "parts": [{"text": message}]})

    # ── Chat History persistence ──
    # Current chat nahi hai to naya banao (pehla message = auto title).
    chat_id = get_chat_id()
    if not chat_id:
        chat_id = save_chat.create_chat(title=save_chat.auto_title(message))
        CHAT_IDS[get_sid()] = chat_id
    else:
        # Chat auto-title: 'New Chat' default title ho to pehle real message se set karo
        save_chat.set_title_if_new(chat_id, save_chat.auto_title(message))
    save_chat.add_message(chat_id, "user", message)

    def generate():
        MAX_ROUNDS = 9999  # practically unlimited
        had_any_commands = False

        yield sse("chat_id", {"chat_id": chat_id})

        for round_num in range(1, MAX_ROUNDS + 1):

            # ── AI se reply lo ──
            if round_num == 1:
                yield sse("phase", {"phase": "thinking"})
            else:
                yield sse("phase", {"phase": "thinking"})
                yield sse("status", {"message": f"🔄 Step {round_num}: next command soch raha hai..."})

            reply = ""
            for kind, text in gemini_stream(api_key, history):
                if kind == "error":
                    yield sse("error", {"message": text})
                    return
                elif kind == "retry":
                    yield sse("status", {"message": "⏳ " + text})
                    continue
                reply += text
                # Round 1 = fresh bubble, baad ke rounds = final bubble me append
                yield sse("token", {"text": text, "final": round_num > 1})

            if not reply.strip():
                yield sse("error", {"message": "Model ne khali reply bheja"})
                return

            # ── Commands dhundo ──
            yield sse("phase", {"phase": "executing"})
            cleaned, had_commands, outputs = extract_and_run_commands(reply)
            history.append({"role": "model", "parts": [{"text": reply}]})
            save_chat.add_message(chat_id, "model", reply)

            if not had_commands:
                # Koi aur command nahi — chain complete!
                break

            had_any_commands = True

            # ── Commands ki output dikhao ──
            for cmd, output in outputs.items():
                yield sse("command", {"cmd": cmd, "output": output})

            # ── Output AI ko wapas do aur loop chalaao ──
            yield sse("status", {"message": f"⚡ Step {round_num} done — aagla step shuru..."})
            time.sleep(3)  # rate limit se bachao

            followup = build_followup_message(reply, outputs)
            history.append({"role": "user", "parts": [{"text": followup}]})
            # Followup context bhi save karo taaki chat continue karte waqt
            # AI ko command outputs ka pura context mile.
            save_chat.add_message(chat_id, "user", followup)

        else:
            yield sse("status", {"message": "⚠️ Max steps reach ho gaye (10)"})

        yield sse("done", {"had_commands": had_any_commands, "chat_id": chat_id})

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    print("\n⚡ AI Agent Web — http://0.0.0.0:%s\n" % port)
    app.run(host="0.0.0.0", port=port, threaded=True)
