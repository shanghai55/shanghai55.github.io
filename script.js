/**
 * Persona Studio — Web frontend
 * Connects to the local API server (web_server.py) in the persona-studio folder.
 */

const API_BASE = window.location.origin;
const MOOD_LABELS = {
  default: "😊 Default",
  happy: "😄 Happy",
  flirty: "😏 Flirty",
  romantic: "💕 Romantic",
  seductive: "🔥 Seductive",
  hypnotized: "🌀 Hypnotized",
  dominant: "👑 Dominant",
  submissive: "🌸 Submissive",
  playful: "🎭 Playful",
  jealous: "💢 Jealous",
  mysterious: "🌙 Mysterious",
  protective: "🛡 Protective",
  serious: "🎯 Serious",
  shy: "🫣 Shy",
  angry: "😤 Angry",
  sad: "😢 Sad",
  nostalgic: "📼 Nostalgic",
  chaotic: "⚡ Chaotic",
};

const RELATIONSHIP_LABELS = {
  stranger: "👋 Stranger",
  acquaintance: "🤝 Acquaintance",
  friend: "😊 Friend",
  close_friend: "💬 Close Friend",
  bestie: "✨ Bestie",
  crush: "💘 Crush",
  fling: "💋 Fling",
  partner: "❤️ Partner",
  soulmate: "💫 Soulmate",
  rival: "⚔️ Rival",
  mentor: "📚 Mentor",
  fan: "⭐ Fan",
};

let pendingFiles = [];
let isLoading = false;

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: options.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || data.detail || `Request failed (${res.status})`);
  }
  return data;
}

function imageUrl(path) {
  if (!path) return null;
  const match = String(path).match(/[\\/](uploads|generated)[\\/]([^\\/]+)$/);
  return match ? `/api/files/${match[1]}/${match[2]}` : null;
}

function showToast(message, duration = 3200) {
  const toast = $("toast");
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => {
    toast.hidden = true;
  }, duration);
}

function setLoading(active) {
  isLoading = active;
  $("loading").hidden = !active;
  document.querySelectorAll(".btn, .chip, .select, .input, .textarea").forEach((el) => {
    if (el.id !== "chat-input") el.disabled = active;
  });
}

function fillSelect(select, choices, value) {
  select.innerHTML = "";
  for (const [label, key] of choices) {
    const opt = document.createElement("option");
    opt.value = key;
    opt.textContent = label;
    select.appendChild(opt);
  }
  if (value != null) select.value = value;
}

function renderMessageContent(content) {
  if (typeof content === "string") {
    const div = document.createElement("div");
    div.textContent = content;
    return div;
  }
  if (!Array.isArray(content)) return document.createTextNode(String(content));

  const wrap = document.createElement("div");
  for (const item of content) {
    if (typeof item === "string") {
      const p = document.createElement("p");
      p.textContent = item;
      p.style.margin = "0 0 6px";
      wrap.appendChild(p);
    } else if (item && item.path) {
      const src = imageUrl(item.path);
      if (src) {
        const img = document.createElement("img");
        img.src = src;
        img.alt = "Shared image";
        img.loading = "lazy";
        wrap.appendChild(img);
      }
    }
  }
  return wrap;
}

function renderChat(messages) {
  const container = $("chat-messages");
  container.innerHTML = "";

  if (!messages || !messages.length) {
    const empty = document.createElement("div");
    empty.className = "empty-chat";
    empty.textContent = "Start a conversation — pick a character and say hello.";
    container.appendChild(empty);
    return;
  }

  for (const msg of messages) {
    const bubble = document.createElement("div");
    bubble.className = `message ${msg.role === "user" ? "user" : "assistant"}`;

    if (msg.speaker && msg.role === "assistant") {
      const speaker = document.createElement("span");
      speaker.className = "speaker";
      speaker.textContent = msg.speaker;
      bubble.appendChild(speaker);
    }

    bubble.appendChild(renderMessageContent(msg.content));
    container.appendChild(bubble);
  }

  container.scrollTop = container.scrollHeight;
}

function renderPersonaList(personas, activeIds) {
  const list = $("persona-list");
  if (!personas.length) {
    list.innerHTML = "<em>No saved characters yet.</em>";
    return;
  }

  list.innerHTML = personas
    .map((p) => {
      const inChat = activeIds.includes(p.id);
      const mood = MOOD_LABELS[p.mood] || p.mood;
      const refs = (p.reference_images || []).length;
      return `<div><span class="${inChat ? "in-chat" : ""}">${inChat ? "✓" : " "}</span> <strong>${escapeHtml(p.name)}</strong> (${p.persona_type}) — ${mood} · ${refs} refs</div>`;
    })
    .join("");
}

function renderRefs(images) {
  const gallery = $("ref-gallery");
  gallery.innerHTML = "";
  for (const path of images || []) {
    const src = imageUrl(path);
    if (!src) continue;
    const img = document.createElement("img");
    img.src = src;
    img.alt = "Reference";
    img.loading = "lazy";
    gallery.appendChild(img);
  }
}

function renderPendingImages() {
  const wrap = $("pending-images");
  wrap.innerHTML = "";
  pendingFiles.forEach((file, index) => {
    const thumb = document.createElement("div");
    thumb.className = "pending-thumb";
    const img = document.createElement("img");
    img.src = URL.createObjectURL(file);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = "×";
    btn.addEventListener("click", () => {
      pendingFiles.splice(index, 1);
      renderPendingImages();
    });
    thumb.append(img, btn);
    wrap.appendChild(thumb);
  });
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function updateSummary(session) {
  const summary = $("chat-summary");
  if (!session.active_personas?.length) {
    summary.textContent = "No characters in chat — toggle In this chat in the sidebar to add them.";
    return;
  }

  const names = session.active_personas.map((p) => p.name).join(", ");
  const reply = session.reply_as_name || "Auto / @mention";
  const lines = [`In chat: ${names}`, `Replies as: ${reply}`];

  if (session.vibe_line) lines.push(session.vibe_line);
  if (session.scene) {
    const scene = session.scene.length > 120 ? session.scene.slice(0, 120) + "…" : session.scene;
    lines.push(`Scene: ${scene}`);
  }
  if (session.memory_line) lines.push(session.memory_line);

  summary.innerHTML = lines.map((l) => `<div>${escapeHtml(l)}</div>`).join("");
}

function updateContext(text) {
  const box = $("context-box");
  if (text) {
    box.textContent = text;
    box.hidden = false;
  } else {
    box.hidden = true;
    box.textContent = "";
  }
}

function updateStatus(status) {
  const apiRow = $("api-status");
  apiRow.innerHTML = "";

  const apiPill = document.createElement("span");
  apiPill.className = `status-pill${status.api_connected ? "" : " offline"}`;
  apiPill.textContent = status.api_connected
    ? "● API connected — Grok chat, images, research, memory"
    : "● No API key — set XAI_API_KEY or run grok to sign in";
  apiRow.appendChild(apiPill);

  const serverPill = $("server-status");
  serverPill.className = "status-pill";
  serverPill.textContent = "● Web server running";

  const links = $("links-banner");
  if (status.urls) {
    links.hidden = false;
    const parts = [`<strong>Your links:</strong>`];
    if (status.urls.local) parts.push(`This device: <a href="${status.urls.local}" target="_blank">${status.urls.local}</a>`);
    if (status.urls.lan) parts.push(`Same Wi‑Fi: <a href="${status.urls.lan}" target="_blank">${status.urls.lan}</a>`);
    if (status.urls.public) parts.push(`Public: <a href="${status.urls.public}" target="_blank">${status.urls.public}</a>`);
    links.innerHTML = parts.join("<br>");
  } else {
    links.hidden = true;
  }

  const gradioBtn = $("btn-open-app");
  if (status.gradio_url) {
    gradioBtn.hidden = false;
    gradioBtn.onclick = () => window.open(status.gradio_url, "_blank");
  } else {
    gradioBtn.hidden = true;
  }
}

async function refreshSession() {
  const session = await api("/api/session");
  const personas = session.personas || [];

  const personaSelect = $("persona-select");
  personaSelect.innerHTML = "";
  for (const p of personas) {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.textContent = `${p.name} (${p.persona_type})`;
    personaSelect.appendChild(opt);
  }
  if (session.selected_persona_id) personaSelect.value = session.selected_persona_id;

  $("in-chat-toggle").checked = session.in_chat || false;

  renderPersonaList(personas, session.active_persona_ids || []);
  renderChat(session.chat_history || []);
  updateSummary(session);
  updateContext("");

  fillSelect($("session-mood"), session.mood_choices || [], session.mood || "default");
  fillSelect($("session-relationship"), session.relationship_choices || [], session.relationship || "friend");
  $("session-intensity").value = session.intensity ?? 5;
  $("intensity-value").textContent = `${session.intensity ?? 5}/10`;
  $("vibe-status").textContent = session.vibe_status || "";

  $("scene-input").value = session.scene || "";
  $("multi-respond").checked = session.multi_respond || false;

  const replySelect = $("reply-as");
  replySelect.innerHTML = "";
  for (const p of session.active_personas || []) {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.textContent = p.name;
    replySelect.appendChild(opt);
  }
  if (session.reply_as_id) replySelect.value = session.reply_as_id;

  if (session.selected_persona) {
    $("ref-count").textContent = session.ref_count_label || "0/10 references";
    renderRefs(session.selected_persona.reference_images);
  }
}

async function init() {
  try {
    fillSelect(
      $("session-mood"),
      Object.entries(MOOD_LABELS),
      "default"
    );
    fillSelect(
      $("session-relationship"),
      Object.entries(RELATIONSHIP_LABELS),
      "friend"
    );

    const status = await api("/api/status");
    updateStatus(status);
    await refreshSession();
  } catch (err) {
    $("server-status").className = "status-pill offline";
    $("server-status").textContent = "● Server offline — run: python web_server.py";
    showToast(`Cannot connect: ${err.message}`);
  }
}

async function selectPersona(id) {
  if (!id) return;
  setLoading(true);
  try {
    await api("/api/session/select", {
      method: "POST",
      body: JSON.stringify({ persona_id: id }),
    });
    await refreshSession();
  } catch (err) {
    showToast(err.message);
  } finally {
    setLoading(false);
  }
}

async function sendMessage(text, moodOverride) {
  const message = (text || $("chat-input").value).trim();
  if (!message && !pendingFiles.length) return;

  setLoading(true);
  try {
    let body;
    if (pendingFiles.length) {
      const form = new FormData();
      form.append("message", message || "(sent a photo)");
      form.append("use_search", $("search-toggle").checked);
      form.append("reply_as_id", $("reply-as").value || "");
      form.append("scene", $("scene-input").value);
      form.append("multi_respond", $("multi-respond").checked);
      if (moodOverride) form.append("mood", moodOverride);
      pendingFiles.forEach((f) => form.append("images", f));
      body = form;
    } else {
      body = JSON.stringify({
        message,
        use_search: $("search-toggle").checked,
        reply_as_id: $("reply-as").value || null,
        scene: $("scene-input").value,
        multi_respond: $("multi-respond").checked,
        mood: moodOverride || null,
      });
    }

    const result = await api("/api/chat", { method: "POST", body });
    renderChat(result.chat_history);
    updateSummary(result);
    updateContext(result.context || "");
    if (result.suggestions?.length) {
      fillSelect($("suggestion-select"), result.suggestions.map((s) => [s, s]), null);
    }
    $("chat-input").value = "";
    pendingFiles = [];
    renderPendingImages();
  } catch (err) {
    showToast(err.message);
  } finally {
    setLoading(false);
  }
}

$("persona-select").addEventListener("change", (e) => selectPersona(e.target.value));

$("in-chat-toggle").addEventListener("change", async (e) => {
  setLoading(true);
  try {
    await api("/api/session/toggle-chat", {
      method: "POST",
      body: JSON.stringify({ in_chat: e.target.checked }),
    });
    await refreshSession();
  } catch (err) {
    showToast(err.message);
    e.target.checked = !e.target.checked;
  } finally {
    setLoading(false);
  }
});

$("btn-create").addEventListener("click", async () => {
  const name = $("new-name").value.trim();
  if (!name) {
    showToast("Enter a character name.");
    return;
  }
  setLoading(true);
  try {
    await api("/api/personas", {
      method: "POST",
      body: JSON.stringify({
        name,
        persona_type: $("new-type").value,
        language_mode: $("new-lang").value,
        auto_research: $("auto-research").checked,
      }),
    });
    $("new-name").value = "";
    showToast(`Created ${name}`);
    await refreshSession();
  } catch (err) {
    showToast(err.message);
  } finally {
    setLoading(false);
  }
});

$("btn-delete").addEventListener("click", async () => {
  const id = $("persona-select").value;
  if (!id || !confirm("Delete this character?")) return;
  setLoading(true);
  try {
    await api(`/api/personas/${id}`, { method: "DELETE" });
    showToast("Character deleted.");
    await refreshSession();
  } catch (err) {
    showToast(err.message);
  } finally {
    setLoading(false);
  }
});

$("btn-duplicate").addEventListener("click", async () => {
  const id = $("persona-select").value;
  if (!id) return;
  setLoading(true);
  try {
    await api(`/api/personas/${id}/duplicate`, { method: "POST" });
    showToast("Character duplicated.");
    await refreshSession();
  } catch (err) {
    showToast(err.message);
  } finally {
    setLoading(false);
  }
});

async function updateVibe() {
  setLoading(true);
  try {
    const result = await api("/api/session/vibe", {
      method: "POST",
      body: JSON.stringify({
        mood: $("session-mood").value,
        relationship: $("session-relationship").value,
        intensity: parseInt($("session-intensity").value, 10),
      }),
    });
    $("vibe-status").textContent = result.vibe_status || "";
    await refreshSession();
  } catch (err) {
    showToast(err.message);
  } finally {
    setLoading(false);
  }
}

$("session-mood").addEventListener("change", updateVibe);
$("session-relationship").addEventListener("change", updateVibe);
$("session-intensity").addEventListener("input", (e) => {
  $("intensity-value").textContent = `${e.target.value}/10`;
});
$("session-intensity").addEventListener("change", updateVibe);

$("scene-input").addEventListener("change", async () => {
  try {
    await api("/api/session/scene", {
      method: "POST",
      body: JSON.stringify({ scene: $("scene-input").value }),
    });
    await refreshSession();
  } catch (err) {
    showToast(err.message);
  }
});

$("reply-as").addEventListener("change", async () => {
  try {
    await api("/api/session/reply-as", {
      method: "POST",
      body: JSON.stringify({ reply_as_id: $("reply-as").value }),
    });
    await refreshSession();
  } catch (err) {
    showToast(err.message);
  }
});

$("btn-send").addEventListener("click", () => sendMessage());
$("chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

$("btn-regen").addEventListener("click", async () => {
  setLoading(true);
  try {
    const result = await api("/api/chat/regenerate", {
      method: "POST",
      body: JSON.stringify({
        use_search: $("search-toggle").checked,
        reply_as_id: $("reply-as").value || null,
        scene: $("scene-input").value,
        multi_respond: $("multi-respond").checked,
      }),
    });
    renderChat(result.chat_history);
    updateSummary(result);
    updateContext(result.context || "");
  } catch (err) {
    showToast(err.message);
  } finally {
    setLoading(false);
  }
});

$("btn-clear").addEventListener("click", async () => {
  if (!confirm("Clear this chat?")) return;
  setLoading(true);
  try {
    await api("/api/chat/clear", { method: "POST" });
    await refreshSession();
  } catch (err) {
    showToast(err.message);
  } finally {
    setLoading(false);
  }
});

$("btn-suggest").addEventListener("click", async () => {
  setLoading(true);
  try {
    const result = await api("/api/chat/suggestions");
    if (result.suggestions?.length) {
      fillSelect($("suggestion-select"), result.suggestions.map((s) => [s, s]), null);
      updateContext(result.message);
    } else {
      updateContext(result.message || "No suggestions right now.");
    }
  } catch (err) {
    showToast(err.message);
  } finally {
    setLoading(false);
  }
});

$("btn-use-suggestion").addEventListener("click", () => {
  const val = $("suggestion-select").value;
  if (val) $("chat-input").value = val;
});

document.querySelectorAll("#vibe-chips .chip").forEach((chip) => {
  chip.addEventListener("click", async () => {
    const mood = chip.dataset.mood;
    const msg = chip.dataset.msg;
    $("session-mood").value = mood;
    await updateVibe();
    sendMessage(msg, mood);
  });
});

document.querySelectorAll("#action-chips .chip").forEach((chip) => {
  chip.addEventListener("click", async () => {
    if (chip.dataset.intense) {
      $("session-intensity").value = 9;
      $("intensity-value").textContent = "9/10";
      await updateVibe();
      sendMessage("Give me your rawest, most intense reply — no holding back.");
      return;
    }
    sendMessage(chip.dataset.msg);
  });
});

$("image-upload").addEventListener("change", (e) => {
  pendingFiles.push(...Array.from(e.target.files));
  e.target.value = "";
  renderPendingImages();
});

$("chat-input").addEventListener("paste", (e) => {
  const items = e.clipboardData?.items;
  if (!items) return;
  for (const item of items) {
    if (item.type.startsWith("image/")) {
      const file = item.getAsFile();
      if (file) pendingFiles.push(file);
    }
  }
  if (pendingFiles.length) renderPendingImages();
});

$("ref-upload").addEventListener("change", async (e) => {
  const id = $("persona-select").value;
  if (!id) {
    showToast("Select a character first.");
    return;
  }
  const files = Array.from(e.target.files);
  if (!files.length) return;

  const form = new FormData();
  files.forEach((f) => form.append("images", f));

  setLoading(true);
  try {
    const result = await api(`/api/personas/${id}/references`, {
      method: "POST",
      body: form,
    });
    showToast(result.message);
    await refreshSession();
  } catch (err) {
    showToast(err.message);
  } finally {
    setLoading(false);
    e.target.value = "";
  }
});

init();