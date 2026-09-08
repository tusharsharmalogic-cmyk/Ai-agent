const $ = (id) => document.getElementById(id);
const chat = $("chat");
const input = $("input");
const btnSend = $("btn-send");

let streaming = false;
let welcomeHTML = "";

// ─── Chat History state ─────────────────────────────────────────────────
let currentChatId = null;
let chatsLoaded = false;

// ─── Utils ────────────────────────────────────────────────────────────────
const esc = (s) =>
  String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");

function md(text) {
  text = text.replace(/^RUN_CMD:\s*(.+)$/gm, "⚡ **Running:** `$1`");
  if (window.marked) {
    try { return marked.parse(text, { breaks: true, gfm: true }); } catch (e) {}
  }
  return esc(text).replace(/\n/g, "<br>");
}

function scrollDown() { chat.scrollTop = chat.scrollHeight; }

function setStatus(phase) {
  const dot = $("status-dot");
  const t = $("status-text");
  dot.className = "status-dot";
  if (phase === "thinking")  { dot.classList.add("busy"); t.textContent = "Soch raha hai…"; }
  else if (phase === "executing") { dot.classList.add("busy"); t.textContent = "Running…"; }
  else if (phase === "final")  { dot.classList.add("busy"); t.textContent = "Writing…"; }
  else if (phase === "error")  { dot.classList.add("err");  t.textContent = "Error"; }
  else { t.textContent = "Ready"; }
}

// ─── Message elements ─────────────────────────────────────────────────────
function addUserMsg(text) {
  const el = document.createElement("div");
  el.className = "msg user";
  el.innerHTML = `<div class="msg-avatar">U</div><div class="bubble">${esc(text)}</div>`;
  chat.appendChild(el);
  scrollDown();
}

function addAiMsg() {
  const el = document.createElement("div");
  el.className = "msg ai";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML = '<span class="cursor"></span>';
  el.innerHTML = `<div class="msg-avatar">✦</div>`;
  el.appendChild(bubble);
  chat.appendChild(el);
  scrollDown();

  let text = "", finished = false;
  return {
    set(t) {
      text = t;
      bubble.innerHTML = md(text) + (finished ? "" : '<span class="cursor"></span>');
      scrollDown();
    },
    done() {
      finished = true;
      bubble.innerHTML = md(text) || "<i style='color:var(--text-muted)'>…</i>";
      scrollDown();
    },
  };
}

function addCmdCard(cmd, output) {
  const el = document.createElement("div");
  el.className = "cmd-card";
  el.innerHTML =
    `<div class="cmd-head"><span>⚡</span> <code>${esc(cmd)}</code></div>` +
    `<pre class="cmd-out">${esc(output)}</pre>`;
  chat.appendChild(el);
  scrollDown();
}

function addErrorMsg(t) {
  const el = document.createElement("div");
  el.className = "msg err";
  el.textContent = t;
  chat.appendChild(el);
  scrollDown();
}

// ─── SSE parsing ──────────────────────────────────────────────────────────
function parseSse(raw) {
  let event = "message", data = "";
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (!data) return null;
  try { return { event, data: JSON.parse(data) }; } catch (e) { return null; }
}

// ─── Send ─────────────────────────────────────────────────────────────────
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
      body: JSON.stringify({ message: text, chat_id: currentChatId }),
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
          const t = $("status-text");
          if (t) t.textContent = ev.data.message;
        } else if (ev.event === "chat_id") {
          // Backend ne naya chat bana (auto-title) — sidebar refresh
          currentChatId = ev.data.chat_id;
          loadChatList($("chat-search").value.trim());
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
          // Sidebar list refresh (ordering / last activity update)
          loadChatList($("chat-search").value.trim());
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

// ─── Restore history ──────────────────────────────────────────────────────
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
      if (m.role === "user") addUserMsg(text);
      else { const b = addAiMsg(); b.set(text); b.done(); }
    }
    if (messages.length) loadChatList();
  } catch (e) {}
}

// ─── Chat History (SQLite persistence) ───────────────────────────────────
async function loadChatList(search = "") {
  const listEl = $("chat-list");
  const emptyEl = $("chat-list-empty");
  try {
    const url = "/api/chats" + (search ? "?q=" + encodeURIComponent(search) : "");
    const { groups } = await (await fetch(url)).json();
    listEl.innerHTML = "";

    let total = 0;
    const order = ["Pinned", "Today", "Yesterday", "Older"];
    for (const groupName of order) {
      const chats = groups[groupName] || [];
      if (!chats.length) continue;
      total += chats.length;

      const label = document.createElement("div");
      label.className = "chat-group-label";
      label.textContent = groupName;
      listEl.appendChild(label);

      for (const c of chats) {
        listEl.appendChild(renderChatItem(c));
      }
    }

    emptyEl.classList.toggle("hidden", total > 0);
  } catch (e) {}
}

function renderChatItem(c) {
  const item = document.createElement("div");
  item.className = "chat-item" + (c.id === currentChatId ? " active" : "") + (c.pinned ? " pinned" : "");
  item.dataset.chatId = c.id;

  const icon = document.createElement("span");
  icon.className = "chat-item-icon";
  icon.textContent = c.pinned ? "📌" : "💬";

  const body = document.createElement("div");
  body.className = "chat-item-body";

  const title = document.createElement("div");
  title.className = "chat-item-title";
  title.textContent = c.title || "New Chat";

  const time = document.createElement("div");
  time.className = "chat-item-time";
  time.textContent = timeAgo(c.updated_at) + " · " + (c.message_count || 0) + " msgs";

  const menuBtn = document.createElement("button");
  menuBtn.className = "chat-menu-btn";
  menuBtn.title = "Options";
  menuBtn.textContent = "⋮";
  menuBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    showChatMenu(e, c);
  });

  body.appendChild(title);
  body.appendChild(time);
  item.appendChild(icon);
  item.appendChild(body);
  item.appendChild(menuBtn);

  item.addEventListener("click", () => openChat(c.id));
  return item;
}

function timeAgo(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const diff = Date.now() - d.getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return mins + "m ago";
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return hrs + "h ago";
  const days = Math.floor(hrs / 24);
  if (days < 7) return days + "d ago";
  return d.toLocaleDateString();
}

async function openChat(chatId) {
  if (streaming) return;
  try {
    const res = await fetch(`/api/chats/${chatId}/open`, { method: "POST" });
    if (!res.ok) return;
    const { chat, messages } = await res.json();
    currentChatId = chatId;
    closeSidebar();

    chat.innerHTML = "";

    for (const m of messages) {
      // [System: ...] followups internal hain — UI me mat dikhao
      if (m.role === "user" && m.content.startsWith("[System:")) continue;
      if (m.role === "user") addUserMsg(m.content);
      else { const b = addAiMsg(); b.set(m.content); b.done(); }
    }

    markActiveChatItem();
    setStatus("idle");
    input.focus();
  } catch (e) {}
}

function markActiveChatItem() {
  document.querySelectorAll(".chat-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.chatId === currentChatId);
  });
}

async function newChat() {
  if (streaming) return;
  try {
    const res = await fetch("/api/chats/new", { method: "POST" });
    if (!res.ok) return;
    const { chat_id } = await res.json();
    currentChatId = chat_id;
  } catch (e) {}
  chat.innerHTML = welcomeHTML;
  setStatus("idle");
  input.focus();
  closeSidebar();
  loadChatList();
}

function showChatMenu(e, c) {
  closeChatMenu();
  const menu = document.createElement("div");
  menu.className = "ctx-menu";
  menu.id = "chat-ctx-menu";

  const mk = (label, icon, fn, danger) => {
    const b = document.createElement("button");
    b.className = "ctx-item" + (danger ? " danger" : "");
    b.textContent = icon + "  " + label;
    b.addEventListener("click", () => { closeChatMenu(); fn(); });
    return b;
  };

  menu.appendChild(mk("Rename", "✏️", () => renameChat(c)));
  menu.appendChild(mk(c.pinned ? "Unpin" : "Pin", "📌", () => togglePin(c)));
  menu.appendChild(mk("Delete", "🗑️", () => deleteChat(c), true));

  document.body.appendChild(menu);

  const r = e.currentTarget.getBoundingClientRect();
  const mw = menu.offsetWidth || 160;
  const mh = menu.offsetHeight || 120;
  let x = Math.min(r.left, window.innerWidth - mw - 8);
  let y = r.bottom + 4;
  if (y + mh > window.innerHeight - 8) y = r.top - mh - 4;
  menu.style.left = Math.max(8, x) + "px";
  menu.style.top = Math.max(8, y) + "px";

  e.currentTarget.closest(".chat-item")?.classList.add("menu-open");
  setTimeout(() => {
    document.addEventListener("click", closeChatMenu, { once: true });
    window.addEventListener("resize", closeChatMenu, { once: true });
  }, 0);
}

function closeChatMenu() {
  $("chat-ctx-menu")?.remove();
  document.querySelectorAll(".chat-item.menu-open").forEach((el) => el.classList.remove("menu-open"));
}

async function renameChat(c) {
  const t = prompt("Naya title:", c.title || "");
  if (t === null) return;
  const title = t.trim();
  if (!title) return;
  try {
    await fetch(`/api/chats/${c.id}/rename`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    loadChatList($("chat-search").value.trim());
  } catch (e) {}
}

async function togglePin(c) {
  try {
    await fetch(`/api/chats/${c.id}/pin`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pinned: !c.pinned }),
    });
    loadChatList($("chat-search").value.trim());
  } catch (e) {}
}

async function deleteChat(c) {
  if (!confirm("Ye chat aur uske saare messages delete ho jayenge. Confirm?")) return;
  try {
    await fetch(`/api/chats/${c.id}/delete`, { method: "POST" });
    if (c.id === currentChatId) {
      currentChatId = null;
      chat.innerHTML = welcomeHTML;
    }
    loadChatList($("chat-search").value.trim());
  } catch (e) {}
}

// Sidebar open/close (mobile drawer)
function openSidebar() {
  $("sidebar").classList.add("open");
  $("sidebar-backdrop")?.classList.add("show");
}
function closeSidebar() {
  $("sidebar").classList.remove("open");
  $("sidebar-backdrop")?.classList.remove("show");
}

// ─── Key overlay (quick-add) ────────────────────────────────────────────
function showKeyOverlay(required) {
  $("key-overlay").classList.remove("hidden");
  $("key-cancel").style.display = required ? "none" : "";
  $("key-err").classList.add("hidden");
  $("key-input").value = "";
  setTimeout(() => $("key-input").focus(), 50);
}
function hideKeyOverlay() { $("key-overlay").classList.add("hidden"); }

async function saveKey() {
  const key = $("key-input").value.trim();
  const errEl = $("key-err");
  if (!key) { errEl.textContent = "Key likhna zaroori hai"; errEl.classList.remove("hidden"); return; }
  try {
    const res = await fetch("/api/key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) { errEl.textContent = j.error || "Save fail hua"; errEl.classList.remove("hidden"); return; }
    hideKeyOverlay();
    // Agar Settings bhi open hai toh refresh karo
    if (settingsOpen) loadSettingsKeys();
    setStatus("idle");
    input.focus();
  } catch (e) { errEl.textContent = "Network error"; errEl.classList.remove("hidden"); }
}

// ─── Settings overlay ───────────────────────────────────────────────────
let settingsOpen = false;

function showSettings() {
  if (settingsOpen) return;
  settingsOpen = true;
  $("settings-overlay").classList.remove("hidden");
  loadSettingsKeys();
}
function hideSettings() {
  settingsOpen = false;
  $("settings-overlay").classList.add("hidden");
}

async function loadSettingsKeys() {
  const listEl = $("settings-key-list");
  const emptyEl = $("settings-key-empty");
  const errEl = $("settings-key-err");
  errEl.classList.add("hidden");
  listEl.innerHTML = "";

  try {
    const res = await fetch("/api/keys");
    const data = await res.json();

    // Model info
    try {
      const info = await (await fetch("/api/info")).json();
      $("settings-model").textContent = info.model || "—";
    } catch (e) {}

    if (!data.keys || data.keys.length === 0) {
      emptyEl.classList.remove("hidden");
      return;
    }
    emptyEl.classList.add("hidden");

    data.keys.forEach((k) => {
      const item = document.createElement("div");
      item.className = "key-item" + (k.active ? " active" : "");

      // Number badge
      const numBadge = document.createElement("span");
      numBadge.className = "key-badge num-badge";
      numBadge.textContent = "#" + (k.index + 1);

      // Masked label
      const label = document.createElement("span");
      label.className = "key-label";
      label.textContent = k.masked;

      const actions = document.createElement("div");
      actions.className = "key-actions";

      if (k.active) {
        const activeBadge = document.createElement("span");
        activeBadge.className = "key-badge active-badge";
        activeBadge.textContent = "Active";
        actions.appendChild(activeBadge);
      } else {
        // Switch button
        const switchBtn = document.createElement("button");
        switchBtn.className = "key-btn switch-btn";
        switchBtn.title = "Is key ko active karo";
        switchBtn.textContent = "⚡";
        switchBtn.addEventListener("click", () => switchToKey(k.index));
        actions.appendChild(switchBtn);
      }

      // Delete button
      const delBtn = document.createElement("button");
      delBtn.className = "key-btn delete-btn";
      delBtn.title = "Key delete karo";
      delBtn.textContent = "✕";
      delBtn.addEventListener("click", () => deleteKey(k.index));
      actions.appendChild(delBtn);

      item.appendChild(numBadge);
      item.appendChild(label);
      item.appendChild(actions);
      listEl.appendChild(item);
    });
  } catch (e) {
    emptyEl.classList.add("hidden");
    listEl.innerHTML = '<div style="color:var(--text-muted);font-size:13px;padding:12px;">Load fail ho gaya</div>';
  }
}

async function addSettingsKey() {
  const input = $("settings-key-input");
  const errEl = $("settings-key-err");
  const key = input.value.trim();
  errEl.classList.add("hidden");

  if (!key) {
    errEl.textContent = "Pehle API key daalo";
    errEl.classList.remove("hidden");
    return;
  }

  try {
    const res = await fetch("/api/keys/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) {
      errEl.textContent = j.error || "Add fail hua";
      errEl.classList.remove("hidden");
      return;
    }
    input.value = "";
    // Agar "key required" overlay open tha (configured nahi tha), toh ab hide karo
    hideKeyOverlay();
    loadSettingsKeys();
  } catch (e) {
    errEl.textContent = "Network error";
    errEl.classList.remove("hidden");
  }
}

async function switchToKey(index) {
  try {
    await fetch("/api/keys/switch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index }),
    });
    loadSettingsKeys();
  } catch (e) {}
}

async function deleteKey(index) {
  if (!confirm("Ye key delete ho jayegi. Confirm karo?")) return;
  try {
    await fetch("/api/keys/remove", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index }),
    });
    loadSettingsKeys();
  } catch (e) {}
}

// ─── Reset ────────────────────────────────────────────────────────────────
async function resetChat() {
  if (streaming) return;
  try { await fetch("/api/reset", { method: "POST" }); } catch (e) {}
  await newChat();
}

// ─── Textarea auto-grow ───────────────────────────────────────────────────
function autoGrow() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 180) + "px";
}

// ─── Wire up ──────────────────────────────────────────────────────────────
btnSend.addEventListener("click", send);

// Enter = new line, Send button = send (no Shift+Enter needed)
input.addEventListener("keydown", (e) => {
  // Enter without any modifier = new line (default textarea behavior)
  // No special handling — just autoGrow
});

input.addEventListener("input", autoGrow);

// Example buttons
chat.addEventListener("click", (e) => {
  const btn = e.target.closest(".example");
  if (!btn) return;
  input.value = btn.textContent;
  autoGrow();
  send();
});

$("btn-reset").addEventListener("click", resetChat);

// Chat History wiring
$("btn-new-chat").addEventListener("click", newChat);
$("btn-sidebar").addEventListener("click", openSidebar);

let searchTimer = null;
$("chat-search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadChatList($("chat-search").value.trim()), 200);
});

// Search input me Enter dabao to blur (mobile keyboard band)
$("chat-search").addEventListener("keydown", (e) => { if (e.key === "Enter") e.target.blur(); });

$("sidebar-backdrop").addEventListener("click", closeSidebar);
$("key-cancel").addEventListener("click", hideKeyOverlay);
$("key-save").addEventListener("click", saveKey);
$("key-input").addEventListener("keydown", (e) => { if (e.key === "Enter") saveKey(); });

// Settings
$("btn-settings").addEventListener("click", showSettings);
$("settings-close").addEventListener("click", hideSettings);
$("settings-key-add").addEventListener("click", addSettingsKey);
$("settings-key-input").addEventListener("keydown", (e) => { if (e.key === "Enter") addSettingsKey(); });
// Click outside modal to close
$("settings-overlay").addEventListener("click", (e) => {
  if (e.target === $("settings-overlay")) hideSettings();
});

// ─── Init ─────────────────────────────────────────────────────────────────
(async function init() {
  welcomeHTML = $("welcome").outerHTML;
  try {
    const info = await (await fetch("/api/info")).json();
    $("model-name").textContent = info.model ? "· " + info.model : "";
    if (!info.configured) showKeyOverlay(true);
  } catch (e) {}
  await restore();
  setStatus("idle");
  input.focus();
})();
