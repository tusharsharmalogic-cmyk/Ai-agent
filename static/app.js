/* ─── AI Agent Web — frontend logic ─── */
const $ = (id) => document.getElementById(id);
const chat = $("chat");
const input = $("input");
const btnSend = $("btn-send");

let streaming = false;
let welcomeHTML = "";

// ─── utils ────────────────────────────────────────────────────────────────
const esc = (s) =>
  String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

function md(text) {
  // RUN_CMD lines ko ⚡ chips bana do
  text = text.replace(/^RUN_CMD:\s*(.+)$/gm, "⚡ **Running:** `$1`");
  if (window.marked) {
    try {
      return marked.parse(text, { breaks: true, gfm: true });
    } catch (e) { /* fallback below */ }
  }
  return esc(text).replace(/\n/g, "<br>");
}

function scrollDown() {
  chat.scrollTop = chat.scrollHeight;
}

function setStatus(phase) {
  const dot = $("status-dot");
  const t = $("status-text");
  dot.className = "dot";
  if (phase === "thinking") { dot.classList.add("amber"); t.textContent = "Gemini soch raha hai…"; }
  else if (phase === "executing") { dot.classList.add("orange"); t.textContent = "Commands run ho rahe hain…"; }
  else if (phase === "final") { dot.classList.add("amber"); t.textContent = "Final answer likh raha hai…"; }
  else if (phase === "error") { dot.classList.add("red"); t.textContent = "Error"; }
  else { t.textContent = "ready"; }
}

// ─── message elements ─────────────────────────────────────────────────────
function addUserMsg(text) {
  const el = document.createElement("div");
  el.className = "msg user";
  const b = document.createElement("div");
  b.className = "bubble user-bubble";
  b.textContent = text;
  el.appendChild(b);
  chat.appendChild(el);
  scrollDown();
}

function addAiMsg() {
  const wrap = document.createElement("div");
  wrap.className = "msg ai";
  const bubble = document.createElement("div");
  bubble.className = "bubble ai-bubble";
  bubble.innerHTML = '<span class="cursor"></span>';
  wrap.appendChild(bubble);
  chat.appendChild(wrap);
  scrollDown();

  let text = "";
  let finished = false;
  return {
    set(t) {
      text = t;
      bubble.innerHTML = md(text) + (finished ? "" : '<span class="cursor"></span>');
      scrollDown();
    },
    done() {
      finished = true;
      bubble.innerHTML = md(text) || "<i>(khali reply)</i>";
      scrollDown();
    },
  };
}

function addCmdCard(cmd, output) {
  const el = document.createElement("div");
  el.className = "cmd-card";
  el.innerHTML =
    '<div class="cmd-head"><span>⚡</span> <code>' + esc(cmd) + "</code></div>" +
    '<pre class="cmd-out">' + esc(output) + "</pre>";
  chat.appendChild(el);
  scrollDown();
}

function addErrorMsg(t) {
  const el = document.createElement("div");
  el.className = "msg err";
  el.textContent = "⚠️ " + t;
  chat.appendChild(el);
  scrollDown();
}

// ─── SSE parsing ──────────────────────────────────────────────────────────
function parseSse(raw) {
  let event = "message";
  let data = "";
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (!data) return null;
  try { return { event, data: JSON.parse(data) }; } catch (e) { return null; }
}

// ─── send / stream ────────────────────────────────────────────────────────
async function send() {
  const text = input.value.trim();
  if (!text || streaming) return;

  const w = $("welcome");
  if (w) w.remove();

  input.value = "";
  autoGrow();
  streaming = true;
  btnSend.disabled = true;
  addUserMsg(text);

  const firstBubble = addAiMsg();
  let buf = "", finalBuf = "", active = firstBubble;

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    });

    if (!res.ok || !res.body) {
      let msg = "HTTP " + res.status;
      try { const j = await res.json(); if (j.error) msg = j.error; } catch (e) {}
      addErrorMsg(msg);
      return;
    }

    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let sbuf = "";

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      sbuf += dec.decode(value, { stream: true });

      let i;
      while ((i = sbuf.indexOf("\n\n")) >= 0) {
        const raw = sbuf.slice(0, i);
        sbuf = sbuf.slice(i + 2);
        const ev = parseSse(raw);
        if (!ev) continue;

        if (ev.event === "phase") {
          setStatus(ev.data.phase);
          if (ev.data.phase === "final") active = addAiMsg();
        } else if (ev.event === "status") {
          // retry/wait messages status bar mein dikhao
          const t = $("status-text");
          if (t) t.textContent = ev.data.message;
        } else if (ev.event === "token") {
          if (ev.data.final) { finalBuf += ev.data.text; active.set(finalBuf); }
          else { buf += ev.data.text; active.set(buf); }
        } else if (ev.event === "command") {
          addCmdCard(ev.data.cmd, ev.data.output);
        } else if (ev.event === "error") {
          setStatus("error");
          addErrorMsg(ev.data.message);
        } else if (ev.event === "done") {
          active.done();
        }
      }
    }
    active.done();
  } catch (err) {
    setStatus("error");
    addErrorMsg("Connection error: " + err.message);
  } finally {
    streaming = false;
    btnSend.disabled = false;
    setStatus("idle");
    input.focus();
  }
}

// ─── restore previous chat (page reload) ──────────────────────────────────
async function restore() {
  try {
    const res = await fetch("/api/history");
    if (!res.ok) return;
    const { messages } = await res.json();
    if (!messages || !messages.length) return;
    const w = $("welcome");
    if (w) w.remove();
    for (const m of messages) {
      const text = m.parts.map((p) => p.text).join("");
      if (m.role === "user") {
        addUserMsg(text);
      } else {
        const b = addAiMsg();
        b.set(text);
        b.done();
      }
    }
  } catch (e) { /* ignore */ }
}

// ─── API key overlay ──────────────────────────────────────────────────────
function showKeyOverlay(required) {
  $("key-overlay").classList.remove("hidden");
  $("key-cancel").style.display = required ? "none" : "";
  $("key-err").classList.add("hidden");
  $("key-input").value = "";
  setTimeout(() => $("key-input").focus(), 50);
}

function hideKeyOverlay() {
  $("key-overlay").classList.add("hidden");
}

async function saveKey() {
  const key = $("key-input").value.trim();
  const errEl = $("key-err");
  if (!key) {
    errEl.textContent = "Key likhna zaroori hai";
    errEl.classList.remove("hidden");
    return;
  }
  try {
    const res = await fetch("/api/key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) {
      errEl.textContent = j.error || "Save fail hua";
      errEl.classList.remove("hidden");
      return;
    }
    hideKeyOverlay();
    setStatus("idle");
    input.focus();
  } catch (e) {
    errEl.textContent = "Network error";
    errEl.classList.remove("hidden");
  }
}

// ─── reset ────────────────────────────────────────────────────────────────
async function resetChat() {
  if (streaming) return;
  try { await fetch("/api/reset", { method: "POST" }); } catch (e) {}
  chat.innerHTML = welcomeHTML;
  setStatus("idle");
  input.focus();
}

// ─── composer ─────────────────────────────────────────────────────────────
function autoGrow() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 160) + "px";
}

// ─── wire up ──────────────────────────────────────────────────────────────
btnSend.addEventListener("click", send);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});
input.addEventListener("input", autoGrow);

// example buttons (delegation — reset ke baad bhi kaam kare)
chat.addEventListener("click", (e) => {
  const btn = e.target.closest(".example");
  if (!btn) return;
  input.value = btn.textContent;
  autoGrow();
  input.focus();
});

$("btn-reset").addEventListener("click", resetChat);
$("btn-key").addEventListener("click", () => showKeyOverlay(false));
$("key-cancel").addEventListener("click", hideKeyOverlay);
$("key-save").addEventListener("click", saveKey);
$("key-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") saveKey();
});

// ─── init ─────────────────────────────────────────────────────────────────
(async function init() {
  welcomeHTML = $("welcome").outerHTML;
  try {
    const info = await (await fetch("/api/info")).json();
    $("model-name").textContent = info.model;
    if (!info.configured) showKeyOverlay(true);
  } catch (e) { /* ignore */ }
  await restore();
  setStatus("idle");
  input.focus();
})();
