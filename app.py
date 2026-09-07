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

from chat import (
    SYSTEM_PROMPT,
    build_followup_message,
    extract_and_run_commands,
    load_api_key,
    save_api_key,
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "ai-agent-web-dev-secret")

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.7-flash")
API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse"
MAX_HISTORY = 30  # last N messages sent to the API (context trim)

# browser session id -> chat history (in-memory)
SESSIONS = {}


# ─── Helpers ──────────────────────────────────────────────────────────────

def get_sid():
    if "sid" not in session:
        session["sid"] = uuid.uuid4().hex
    return session["sid"]


def get_history():
    return SESSIONS.setdefault(get_sid(), [])


def get_api_key():
    # env var pehle, phir ~/.gemini_config.json (jaisa CLI karta hai)
    return os.environ.get("GEMINI_API_KEY") or load_api_key()


def sse(event, data):
    return "event: %s\ndata: %s\n\n" % (event, json.dumps(data))


def gemini_stream(api_key, history):
    """Gemini SSE ko parse karke ('token', text) / ('error', msg) yield karo."""
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

    response = call()
    if response.status_code == 503:  # busy -> ek retry (CLI jaisa)
        time.sleep(5)
        response = call()

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
    return jsonify(model=MODEL, configured=bool(get_api_key()))


@app.route("/api/key", methods=["POST"])
def key_save():
    data = request.get_json(silent=True) or {}
    key = (data.get("key") or "").strip()
    if not key:
        return jsonify(error="API key required"), 400
    save_api_key(key)
    return jsonify(configured=True)


@app.route("/api/reset", methods=["POST"])
def reset():
    SESSIONS.pop(get_sid(), None)
    return jsonify(ok=True)


@app.route("/api/history", methods=["GET"])
def history():
    # sirf visible turns: system followup messages chhupao
    visible = [
        m for m in get_history()
        if not (m["role"] == "user" and m["parts"][0]["text"].startswith("[System:"))
    ]
    return jsonify(messages=visible)


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

    def generate():
        # ── Phase 1: model ka reply stream karo ──
        yield sse("phase", {"phase": "thinking"})
        reply = ""
        for kind, text in gemini_stream(api_key, history):
            if kind == "error":
                yield sse("error", {"message": text})
                return
            reply += text
            yield sse("token", {"text": text})

        if not reply.strip():
            yield sse("error", {"message": "Model ne khali reply bheja"})
            return

        # ── RUN_CMD lines dhundo aur run karo (CLI jaisa) ──
        yield sse("phase", {"phase": "executing"})
        cleaned, had_commands, outputs = extract_and_run_commands(reply)
        history.append({"role": "model", "parts": [{"text": reply}]})

        for cmd, output in outputs.items():
            yield sse("command", {"cmd": cmd, "output": output})

        if had_commands:
            # ── Phase 2: outputs wapas deke final answer lo ──
            followup = build_followup_message(reply, outputs)
            history.append({"role": "user", "parts": [{"text": followup}]})
            yield sse("phase", {"phase": "final"})
            final_reply = ""
            for kind, text in gemini_stream(api_key, history):
                if kind == "error":
                    yield sse("error", {"message": text})
                    return
                final_reply += text
                yield sse("token", {"text": text, "final": True})
            if final_reply.strip():
                history.append({"role": "model", "parts": [{"text": final_reply}]})

        yield sse("done", {"had_commands": had_commands})

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    print("\n⚡ AI Agent Web — http://0.0.0.0:%s\n" % port)
    app.run(host="0.0.0.0", port=port, threaded=True)
