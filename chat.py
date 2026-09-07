import requests
import json
import os
import subprocess
import re
import time

CONFIG_FILE = os.path.expanduser("~/.gemini_config.json")

# ─── System prompt — AI ko batata hai commands kaise dene hain ───
SYSTEM_PROMPT = """You are a helpful AI assistant running inside Termux on Android.

You have the ability to run shell commands directly on the user's device.

IMPORTANT: Whenever you need to run a command to answer the user, output it in this exact format on its own line:
RUN_CMD: <command here>

Examples:
RUN_CMD: df -h
RUN_CMD: ls -la
RUN_CMD: uname -a
RUN_CMD: cat /proc/meminfo

Rules:
- Use RUN_CMD only when a command will actually help answer the question.
- You can use multiple RUN_CMD lines in one reply if needed.
- After you see command output (provided as COMMAND_OUTPUT: ...), use it to give a clear answer.
- Never run destructive commands like rm -rf, mkfs, dd, etc.
- Respond in the same language the user uses (Hindi/English/Hinglish).
"""

# ─── Dangerous commands blacklist ───
BLACKLIST = [
    r'\brm\s+-rf\b', r'\bmkfs\b', r'\bdd\b', r'\bformat\b',
    r'\bshutdown\b', r'\breboot\b', r'\bpkill\b', r'\bkillall\b',
    r'\bchmod\s+777\b', r'\bwget\b.*\|\s*sh', r'\bcurl\b.*\|\s*sh',
    r'\bsudo\b', r'\bsu\b\s'
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
            timeout=15
        )
        output = result.stdout + result.stderr
        return output.strip() if output.strip() else "(no output)"
    except subprocess.TimeoutExpired:
        return "[ERROR] Command timeout ho gaya (15s)"
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
    with open(CONFIG_FILE, "w") as f:
        json.dump({"api_key": key}, f)
    print("✓ API key saved!")

def load_api_key():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)["api_key"]
    return None

def chat(api_key, history):
    MODEL = "gemini-3.7-flash"
    URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:streamGenerateContent?alt=sse"
    PAYLOAD = {
        "system_instruction": {
            "role": "system",
            "parts": [{"text": SYSTEM_PROMPT}]
        },
        "contents": history
    }

    response = requests.post(URL, params={"key": api_key}, json=PAYLOAD, stream=True)

    # 503 pe retry
    if response.status_code == 503:
        print("\n[Server busy, 5 sec baad retry kar raha hoon...]")
        time.sleep(5)
        response = requests.post(URL, params={"key": api_key}, json=PAYLOAD, stream=True)

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

        reply = chat(api_key, history)
        if not reply:
            continue

        # Commands dhundho aur run karo
        cleaned_reply, had_commands, outputs = extract_and_run_commands(reply)

        if had_commands:
            # Original reply history me daalo
            history.append({"role": "model", "parts": [{"text": reply}]})

            # Command outputs AI ko wapas do aur final answer lo
            followup = build_followup_message(reply, outputs)
            history.append({"role": "user", "parts": [{"text": followup}]})

            print("Gemini (final answer): ", end="", flush=True)
            final_reply = chat(api_key, history)
            if final_reply:
                history.append({"role": "model", "parts": [{"text": final_reply}]})
        else:
            history.append({"role": "model", "parts": [{"text": reply}]})

if __name__ == "__main__":
    main()
