import requests
import json
import os
import subprocess
import re
import time

CONFIG_FILE = os.path.expanduser("~/.gemini_config.json")

# ─── Multi-key storage helpers ───
def _load_config():
    """Config file se full data load karo."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            data = json.load(f)
            # Migration: purana single-key format se naye format me
            if "api_key" in data and "keys" not in data:
                data = {
                    "keys": [data["api_key"]] if data["api_key"] else [],
                    "active_index": 0,
                }
                _save_config(data)
            return data
    return {"keys": [], "active_index": 0}


def _save_config(data):
    """Config data ko file me save karo."""
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_all_keys():
    """Saari keys list me return karo."""
    return _load_config().get("keys", [])


def get_active_index():
    """Active key index return karo."""
    cfg = _load_config()
    keys = cfg.get("keys", [])
    idx = cfg.get("active_index", 0)
    if not keys:
        return -1
    if idx < 0 or idx >= len(keys):
        return 0
    return idx


def add_key(new_key):
    """Nayi key add karo. Pehli key ho to usse active banao."""
    cfg = _load_config()
    keys = cfg.get("keys", [])
    if new_key in keys:
        return False, "Ye key pehle se hai"
    keys.append(new_key)
    if len(keys) == 1:
        cfg["active_index"] = 0
    cfg["keys"] = keys
    _save_config(cfg)
    return True, f"Key #{len(keys)} save ho gayi"


def remove_key(index):
    """Index par key delete karo."""
    cfg = _load_config()
    keys = cfg.get("keys", [])
    if index < 0 or index >= len(keys):
        return False, "Galat index"
    keys.pop(index)
    cfg["keys"] = keys
    # Active index adjust karo
    active = cfg.get("active_index", 0)
    if not keys:
        cfg["active_index"] = 0
    elif active >= len(keys):
        cfg["active_index"] = len(keys) - 1
    elif active > index:
        cfg["active_index"] = active - 1
    _save_config(cfg)
    return True, f"Key #{index + 1} delete ho gayi"


def switch_key(index):
    """Active key index change karo."""
    cfg = _load_config()
    keys = cfg.get("keys", [])
    if index < 0 or index >= len(keys):
        return False, "Galat index"
    cfg["active_index"] = index
    _save_config(cfg)
    return True, f"Key #{index + 1} active ho gayi"


def mask_key(key):
    """Key ko masked form me convert karo (e.g. AIza...xyz)."""
    if len(key) <= 8:
        return key[:3] + "***"
    return key[:5] + "..." + key[-4:]


# ─── System prompt — AI ko batata hai commands kaise dene hain ───
SYSTEM_PROMPT = """You are a helpful AI assistant running inside Termux on Android.

You have the ability to run shell commands directly on the user's device.

IMPORTANT: Whenever you need to run a command, output it in this exact format on its own line:
RUN_CMD: <command here>

You can use multiple RUN_CMD lines in one reply if needed.

════════════════════════════════════════
FILE CREATION — use printf (NEVER echo for multi-line):
════════════════════════════════════════
RUN_CMD: printf '%s\n' 'line1' 'line2' 'line3' > /path/to/file.txt

For Python/code files with special characters, use cat with a heredoc via printf:
RUN_CMD: printf 'first line\nsecond line\nthird line\n' > /path/to/file.py

To create a directory first:
RUN_CMD: mkdir -p /path/to/dir

════════════════════════════════════════
FILE EDITING — use sed for targeted edits:
════════════════════════════════════════
Replace a specific line/word:
RUN_CMD: sed -i 's/old_text/new_text/g' /path/to/file.txt

Delete a line containing a pattern:
RUN_CMD: sed -i '/pattern_to_delete/d' /path/to/file.txt

Insert a line after a match:
RUN_CMD: sed -i '/match_line/a new line to insert' /path/to/file.txt

Insert a line before a match:
RUN_CMD: sed -i '/match_line/i new line to insert' /path/to/file.txt

Append to end of file:
RUN_CMD: printf 'new content\n' >> /path/to/file.txt

════════════════════════════════════════
READ / VERIFY files:
════════════════════════════════════════
RUN_CMD: cat /path/to/file.txt
RUN_CMD: cat -n /path/to/file.txt   (with line numbers)
RUN_CMD: head -20 /path/to/file.txt
RUN_CMD: tail -20 /path/to/file.txt
RUN_CMD: ls -la /path/to/dir

════════════════════════════════════════
IMPORTANT RULES:
════════════════════════════════════════
- After creating/editing a file, always verify with: RUN_CMD: cat /path/to/file
- Never use bare `echo` for multi-line content — it breaks on special characters
- Never run destructive commands like rm -rf, mkfs, dd, shutdown, reboot
- After seeing COMMAND_OUTPUT, use it to give a clear answer to the user
- Respond in the same language the user uses (Hindi/English/Hinglish)
- If a command fails, read the error carefully and try a corrected version
"""

# ─── Dangerous commands blacklist ───
BLACKLIST = [
    r'\brm\s+-rf\b',           # recursive force delete
    r'\bmkfs\b',               # format filesystem
    r'\bdd\b.*of=/dev/',       # dd to device (specific, not dd command itself)
    r'\bshutdown\b',           # shutdown
    r'\breboot\b',             # reboot
    r'\bwget\b.*\|\s*sh',      # wget pipe to shell
    r'\bcurl\b.*\|\s*sh',      # curl pipe to shell
    r'\bsudo\b',               # sudo (not available in Termux anyway)
    r':\(\)\{.*\}.*:',         # fork bomb
]

def is_dangerous(cmd):
    for pattern in BLACKLIST:
        if re.search(pattern, cmd, re.IGNORECASE):
            return True
    return False

def run_command(cmd):
    cmd = cmd.strip()
    if is_dangerous(cmd):
        return f"[BLOCKED] Ye command dangerous hai, run nahi kiya: {cmd}"
    try:
        result = subprocess.run(
            cmd, shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "HOME": os.path.expanduser("~")}
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if stdout and stderr:
            output = stdout + "\n[stderr]: " + stderr
        elif stdout:
            output = stdout
        elif stderr:
            output = "[stderr]: " + stderr
        else:
            output = "(no output — exit code: %d)" % result.returncode
        return output
    except subprocess.TimeoutExpired:
        return "[ERROR] Command timeout ho gaya (30s)"
    except Exception as e:
        return f"[ERROR] {str(e)}"

def extract_and_run_commands(text):
    """
    Reply me se RUN_CMD lines dhundho, run karo,
    output wapas text me inject karo.
    Returns (cleaned_text, had_commands, outputs_dict)
    """
    pattern = r'^RUN_CMD:\s*(.+)$'
    lines = text.split('\n')
    outputs = {}
    result_lines = []

    for line in lines:
        match = re.match(pattern, line.strip())
        if match:
            cmd = match.group(1).strip()
            print(f"\n⚡ Running: {cmd}")
            output = run_command(cmd)
            print(f"📤 Output:\n{output}\n")
            outputs[cmd] = output
            # Command line ko output ke saath replace karo
            result_lines.append(f"[Ran: `{cmd}`]")
            result_lines.append(f"```\n{output}\n```")
        else:
            result_lines.append(line)

    cleaned = '\n'.join(result_lines)
    return cleaned, len(outputs) > 0, outputs

def build_followup_message(original_reply, outputs):
    """AI ko command outputs deke final answer generate karao"""
    outputs_text = "\n\n".join(
        f"COMMAND_OUTPUT for `{cmd}`:\n{out}"
        for cmd, out in outputs.items()
    )
    return f"[System: Commands run ho gaye. Outputs:\n\n{outputs_text}\n\nAb user ko clear answer do.]"

def save_api_key(key):
    """Legacy single-key save — naye multi-key format me convert karo."""
    cfg = _load_config()
    keys = cfg.get("keys", [])
    if not keys:
        cfg["keys"] = [key]
        cfg["active_index"] = 0
        _save_config(cfg)
    elif key not in keys:
        keys.append(key)
        cfg["keys"] = keys
        cfg["active_index"] = len(keys) - 1
        _save_config(cfg)
    else:
        # Key already exists, usse active banao
        cfg["active_index"] = keys.index(key)
        _save_config(cfg)
    # Verify write
    if os.path.exists(CONFIG_FILE):
        print(f"✓ API key saved! ({len(get_all_keys())} keys total) File: {CONFIG_FILE}")
    else:
        print(f"✗ Config file NOT created at {CONFIG_FILE}")


def get_api_key_env():
    """GEMINI_API_KEY env var se key lo (fallback)."""
    return os.environ.get("GEMINI_API_KEY")


def load_api_key():
    """Active API key return karo."""
    keys = get_all_keys()
    idx = get_active_index()
    if idx >= 0 and idx < len(keys):
        return keys[idx]
    return None

def call_with_retry(call_fn, max_retries=4):
    """429 aur 503 pe exponential backoff ke saath retry karo."""
    for attempt in range(max_retries):
        response = call_fn()
        if response.status_code == 429:
            wait = 5 * (2 ** attempt)  # 5s, 10s, 20s, 40s
            print(f"\n[429 Rate limit] Attempt {attempt+1}/{max_retries} — {wait}s baad retry...")
            time.sleep(wait)
            continue
        if response.status_code == 503:
            wait = 5
            print(f"\n[503 Server busy] {wait}s baad retry...")
            time.sleep(wait)
            continue
        return response
    return response  # last attempt return karo


def chat(api_key, history):
    MODEL = "gemini-3.5-flash-lite"
    URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:streamGenerateContent?alt=sse"
    PAYLOAD = {
        "system_instruction": {
            "role": "system",
            "parts": [{"text": SYSTEM_PROMPT}]
        },
        "contents": history
    }

    response = call_with_retry(
        lambda: requests.post(URL, params={"key": api_key}, json=PAYLOAD, stream=True)
    )

    # HTTP error check
    if response.status_code != 200:
        try:
            err = response.json()
            print(f"\n[API Error {response.status_code}]: {err}")
        except Exception:
            print(f"\n[HTTP Error {response.status_code}]: {response.text[:300]}")
        return None

    full_reply = ""
    print("\nGemini: ", end="", flush=True)

    for line in response.iter_lines():
        if line and line.startswith(b"data: "):
            try:
                data = json.loads(line[6:])
                if "error" in data:
                    print(f"\n[Error]: {data['error']['message']}")
                    return None
                if "candidates" in data:
                    parts = data["candidates"][0].get("content", {}).get("parts", [])
                    for part in parts:
                        chunk = part.get("text", "")
                        print(chunk, end="", flush=True)
                        full_reply += chunk
            except (json.JSONDecodeError, KeyError, IndexError):
                continue

    print("\n")
    return full_reply if full_reply else None

def get_input():
    line = input("You: ").strip()
    if line == '"""':
        print("(Multiline mode — ek baar aur \"\"\" likho finish karne ke liye)")
        lines = []
        while True:
            l = input()
            if l.strip() == '"""':
                break
            lines.append(l)
        return "\n".join(lines)
    return line

def main():
    api_key = load_api_key()

    if not api_key:
        api_key = input("Gemini API key daalo: ").strip()
        save_api_key(api_key)
    else:
        print("✓ API key loaded!")

    print("\nGemini Chat (with Auto Command Execution)")
    print("  quit  — exit")
    print("  reset — naya chat")
    print("  key   — API key badlo")
    print('  \"\"\"   — multiline/code input')
    print("  ⚡ AI khud commands run kar sakta hai\n")

    history = []

    while True:
        try:
            user_input = get_input()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not user_input:
            continue
        elif user_input.lower() == "quit":
            print("Bye!")
            break
        elif user_input.lower() == "reset":
            history = []
            print("✓ Chat reset!\n")
            continue
        elif user_input.lower() == "key":
            api_key = input("Nai API key daalo: ").strip()
            save_api_key(api_key)
            continue

        history.append({
            "role": "user",
            "parts": [{"text": user_input}]
        })

        # ── Recursive command loop: command → ans → command → ans ... ──
        MAX_ROUNDS = 9999
        reply = None
        for round_num in range(1, MAX_ROUNDS + 1):
            if round_num == 1:
                print("Gemini: ", end="", flush=True)
            else:
                print(f"\nGemini (step {round_num}): ", end="", flush=True)

            reply = chat(api_key, history)
            if not reply:
                break

            cleaned_reply, had_commands, outputs = extract_and_run_commands(reply)
            history.append({"role": "model", "parts": [{"text": reply}]})

            if not had_commands:
                break  # Koi aur command nahi — chain complete!

            # Command output AI ko wapas do
            print(f"[⏳ Step {round_num} done — next step ke liye 3s wait...]")
            time.sleep(3)
            followup = build_followup_message(reply, outputs)
            history.append({"role": "user", "parts": [{"text": followup}]})
        else:
            print("\n[⚠️ Max steps (9999) reach ho gaye]")

if __name__ == "__main__":
    main()
