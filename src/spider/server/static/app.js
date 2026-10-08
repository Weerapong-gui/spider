"use strict";

const gate = document.getElementById("gate");
const gateForm = document.getElementById("gate-form");
const gateToken = document.getElementById("gate-token");
const gateError = document.getElementById("gate-error");
const appEl = document.getElementById("app");
const itemsEl = document.getElementById("items");
const emptyEl = document.getElementById("empty");
const searchEl = document.getElementById("search");
const composeEl = document.getElementById("compose");
const textEl = document.getElementById("text");
const fileInput = document.getElementById("file-input");
const progressEl = document.getElementById("progress");
const overlay = document.getElementById("drop-overlay");
const noticeEl = document.getElementById("notice");

const INLINE_OK = (type) => {
  const base = (type || "").split(";")[0].trim().toLowerCase();
  if (base === "image/svg+xml") return false;
  return base.startsWith("image/") || base === "application/pdf" || base === "text/plain";
};

function humanSize(n) {
  const units = ["B", "KB", "MB", "GB"];
  let value = n;
  for (const unit of units) {
    if (value < 1024 || unit === "GB") {
      return unit === "B" ? `${value} B` : `${value.toFixed(1)} ${unit}`;
    }
    value /= 1024;
  }
  return `${n} B`;
}

function humanAge(iso) {
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

async function copyToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  // Plain http on a Tailscale address is not a secure context, so the async
  // clipboard API is missing there. Fall back to a temporary textarea.
  const area = document.createElement("textarea");
  area.value = text;
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.append(area);
  area.select();
  const ok = document.execCommand("copy");
  area.remove();
  if (!ok) throw new Error("copy failed");
}

function notify(message) {
  noticeEl.textContent = message;
  noticeEl.classList.toggle("hidden", !message);
}

function describeError(text) {
  try {
    return JSON.parse(text).error.message;
  } catch {
    return text || "Something went wrong.";
  }
}

function showGate(message) {
  gateError.textContent = message || "";
  gate.classList.remove("hidden");
  appEl.classList.add("hidden");
}

function showApp() {
  gate.classList.add("hidden");
  appEl.classList.remove("hidden");
}

async function api(path, options) {
  const response = await fetch(path, { credentials: "same-origin", ...options });
  if (response.status === 401) {
    showGate("Session expired. Enter the token again.");
    throw new Error("unauthorized");
  }
  return response;
}

gateForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const response = await fetch("/api/session", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: gateToken.value }),
  });
  if (response.ok) {
    gateToken.value = "";
    showApp();
    refresh();
  } else {
    gateError.textContent = "That token is not correct.";
  }
});

// Elements are built with createElement and textContent, never innerHTML, so a
// filename can never turn into markup.
function renderItem(item) {
  const li = document.createElement("li");

  const head = document.createElement("div");
  head.className = "item-head";
  const name = document.createElement("span");
  name.className = "item-name";
  name.textContent = item.name;
  const meta = document.createElement("span");
  meta.className = "item-meta";
  meta.textContent = `${humanSize(item.size)} · ${item.source_device} · ${humanAge(item.created_at)} · ${item.id.slice(0, 12)}`;
  head.append(name, meta);
  li.append(head);

  if (item.kind === "text" && item.preview) {
    const preview = document.createElement("p");
    preview.className = "preview";
    preview.textContent = item.preview;
    preview.addEventListener("click", () => preview.classList.toggle("open"));
    li.append(preview);
  }

  const actions = document.createElement("div");
  actions.className = "actions";

  if (item.kind === "text") {
    const copy = document.createElement("button");
    copy.textContent = "Copy";
    copy.addEventListener("click", async () => {
      const response = await api(`/api/items/${item.id}/content`);
      try {
        await copyToClipboard(await response.text());
        copy.textContent = "Copied";
      } catch {
        copy.textContent = "Copy failed";
      }
      setTimeout(() => { copy.textContent = "Copy"; }, 1500);
    });
    actions.append(copy);
  }

  if (INLINE_OK(item.content_type)) {
    const open = document.createElement("button");
    open.textContent = "Open";
    open.addEventListener("click", () => {
      window.open(`/api/items/${item.id}/content?disposition=inline`, "_blank", "noopener");
    });
    actions.append(open);
  }

  const download = document.createElement("button");
  download.textContent = "Download";
  download.addEventListener("click", () => {
    window.location.href = `/api/items/${item.id}/content`;
  });
  actions.append(download);

  const remove = document.createElement("button");
  remove.className = "danger";
  remove.textContent = "Delete";
  remove.addEventListener("click", async () => {
    const response = await api(`/api/items/${item.id}`, { method: "DELETE" });
    if (!response.ok) notify(describeError(await response.text()));
    refresh();
  });
  actions.append(remove);

  li.append(actions);
  return li;
}

async function refresh() {
  let response;
  try {
    const query = searchEl.value ? `&q=${encodeURIComponent(searchEl.value)}` : "";
    response = await api(`/api/items?limit=100${query}`);
  } catch {
    return;
  }
  const page = await response.json();
  itemsEl.replaceChildren(...page.items.map(renderItem));
  emptyEl.classList.toggle("hidden", page.items.length > 0);
}

function upload(file, name, kind) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("content", file, name);
    form.append("name", name);
    form.append("kind", kind);
    form.append("device", "browser");

    const request = new XMLHttpRequest();
    request.open("POST", "/api/items");
    request.withCredentials = true;
    request.upload.addEventListener("progress", (event) => {
      if (!event.lengthComputable) return;
      progressEl.classList.remove("hidden");
      progressEl.value = (event.loaded / event.total) * 100;
    });
    request.addEventListener("load", () => {
      progressEl.classList.add("hidden");
      progressEl.value = 0;
      if (request.status === 401) { showGate("Enter the token."); reject(new Error("unauthorized")); }
      else if (request.status >= 400) { reject(new Error(describeError(request.responseText))); }
      else { resolve(); }
    });
    request.addEventListener("error", () => {
      progressEl.classList.add("hidden");
      reject(new Error("network error"));
    });
    request.send(form);
  });
}

async function guarded(task) {
  notify("");
  let ok = true;
  try {
    await task();
  } catch (error) {
    ok = false;
    if (error.message !== "unauthorized") notify(`Upload failed: ${error.message}`);
  }
  refresh();
  return ok;
}

function uploadFiles(files) {
  const list = [...files];
  return guarded(async () => {
    for (const file of list) await upload(file, file.name, "file");
  });
}

composeEl.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = textEl.value;
  if (!text.trim()) return;
  const label = text.trim().split("\n")[0].slice(0, 40) || "text";
  // Keep what was typed if the upload failed, so nothing is lost.
  if (await guarded(() => upload(new Blob([text], { type: "text/plain" }), label, "text"))) {
    textEl.value = "";
  }
});

fileInput.addEventListener("change", async () => {
  await uploadFiles(fileInput.files);
  fileInput.value = "";
});

let dragDepth = 0;
window.addEventListener("dragenter", (event) => {
  event.preventDefault();
  dragDepth += 1;
  overlay.classList.remove("hidden");
});
window.addEventListener("dragover", (event) => event.preventDefault());
window.addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) overlay.classList.add("hidden");
});
window.addEventListener("drop", (event) => {
  event.preventDefault();
  dragDepth = 0;
  overlay.classList.add("hidden");
  if (event.dataTransfer.files.length) uploadFiles(event.dataTransfer.files);
});

// Ctrl/Cmd+V anywhere on the page uploads whatever is on the clipboard --
// including images, which the CLI cannot do.
window.addEventListener("paste", async (event) => {
  if (document.activeElement === textEl) return;
  const files = [...event.clipboardData.files];
  if (files.length) {
    event.preventDefault();
    await uploadFiles(files);
    return;
  }
  const text = event.clipboardData.getData("text/plain");
  if (text.trim()) {
    event.preventDefault();
    const label = text.trim().split("\n")[0].slice(0, 40) || "text";
    await guarded(() => upload(new Blob([text], { type: "text/plain" }), label, "text"));
  }
});

let searchTimer;
searchEl.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(refresh, 250);
});

window.addEventListener("focus", refresh);
setInterval(refresh, 10000);

(async () => {
  const response = await fetch("/api/items?limit=1", { credentials: "same-origin" });
  if (response.status === 401) showGate("");
  else { showApp(); refresh(); }
})();
