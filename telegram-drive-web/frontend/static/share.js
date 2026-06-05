const $ = (sel) => document.querySelector(sel);

const SHARE_TOKEN = (() => {
  const m = window.location.pathname.match(/^\/s\/([^/]+)\/?$/);
  return m ? decodeURIComponent(m[1]) : "";
})();

let shareMeta = null;
let sharePage = 1;

const VISIBILITY_HINTS = {
  both: "Pengunjung dapat melihat pratinjau dan mengunduh file.",
  download: "Pengunjung hanya dapat mengunduh (tanpa pratinjau di halaman).",
  preview: "Pengunjung hanya dapat melihat daftar dan pratinjau — unduh dinonaktifkan.",
};

function formatSize(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function show(el) {
  if (el) el.classList.remove("hidden");
}
function hide(el) {
  if (el) el.classList.add("hidden");
}

async function apiPublic(path, options = {}) {
  const r = await fetch(path, { credentials: "same-origin", ...options });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const err = new Error(data.detail || data.message || r.statusText);
    err.status = r.status;
    err.code = typeof data.detail === "string" ? data.detail : "";
    throw err;
  }
  return data;
}

function setState(which) {
  hide($("#share-loading"));
  hide($("#share-error-state"));
  hide($("#share-expired"));
  hide($("#share-password-panel"));
  hide($("#share-content"));
  if (which === "loading") show($("#share-loading"));
  else if (which === "error") show($("#share-error-state"));
  else if (which === "expired") show($("#share-expired"));
  else if (which === "password") show($("#share-password-panel"));
  else if (which === "content") show($("#share-content"));
}

async function loadShareMeta() {
  return apiPublic(`/api/public/s/${encodeURIComponent(SHARE_TOKEN)}`);
}

function applyShareMeta(meta) {
  shareMeta = meta;
  const title = meta.title || (meta.share_type === "file" ? "File dibagikan" : "Folder dibagikan");
  $("#share-title").textContent = title;
  $("#share-password-title").textContent = title;
  const hint = VISIBILITY_HINTS[meta.visibility] || "";
  $("#share-visibility-hint").textContent = hint;
}

async function tryUnlock(password) {
  return apiPublic(`/api/public/s/${encodeURIComponent(SHARE_TOKEN)}/unlock`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
}

async function loadFiles(page = 1) {
  const q = new URLSearchParams({ page: String(page), per_page: "24" });
  return apiPublic(`/api/public/s/${encodeURIComponent(SHARE_TOKEN)}/files?${q}`);
}

function renderShareFiles(data) {
  const grid = $("#share-files-grid");
  const empty = $("#share-files-empty");
  const files = data.files || [];
  if (!files.length) {
    hide(grid);
    show(empty);
    empty.textContent = "Tidak ada file di folder ini.";
    return;
  }
  show(grid);
  hide(empty);
  grid.innerHTML = "";
  const canPreview = shareMeta?.allows_preview;
  const canDownload = shareMeta?.allows_download;
  const base = `/api/public/s/${encodeURIComponent(SHARE_TOKEN)}`;

  files.forEach((f) => {
    const card = document.createElement("article");
    card.className = "share-file-card";
    let actions = "";
    if (canPreview && f.previewable) {
      actions += `<button type="button" class="btn ghost sm" data-preview="${f.id}">Lihat</button>`;
    }
    if (canDownload) {
      actions += `<a class="btn sm" href="${base}/download/${f.id}" download>Download</a>`;
    }
    if (!actions) {
      actions = `<span class="hint">Akses terbatas</span>`;
    }
    card.innerHTML = `
      <div class="name">${escapeHtml(f.name)}</div>
      <div class="meta">${escapeHtml(f.sizeStr || formatSize(f.size))}</div>
      <div class="actions">${actions}</div>`;
    grid.appendChild(card);
  });

  grid.querySelectorAll("[data-preview]").forEach((btn) => {
    btn.onclick = () => openSharePreview(parseInt(btn.dataset.preview, 10), files);
  });

  const pag = $("#share-pagination");
  const totalPages = data.total_pages || 1;
  if (totalPages <= 1) {
    hide(pag);
    return;
  }
  show(pag);
  pag.innerHTML = "";
  const prev = document.createElement("button");
  prev.type = "button";
  prev.className = "btn ghost sm";
  prev.textContent = "←";
  prev.disabled = page <= 1;
  prev.onclick = () => {
    sharePage = Math.max(1, page - 1);
    refreshFiles();
  };
  const label = document.createElement("span");
  label.className = "hint";
  label.textContent = `${page} / ${totalPages}`;
  const next = document.createElement("button");
  next.type = "button";
  next.className = "btn ghost sm";
  next.textContent = "→";
  next.disabled = page >= totalPages;
  next.onclick = () => {
    sharePage = Math.min(totalPages, page + 1);
    refreshFiles();
  };
  pag.append(prev, label, next);
}

async function refreshFiles() {
  try {
    const data = await loadFiles(sharePage);
    renderShareFiles(data);
  } catch (e) {
    if (e.code === "share_password_required") {
      setState("password");
      return;
    }
    throw e;
  }
}

function openSharePreview(messageId, files) {
  const file = files.find((x) => x.id === messageId);
  if (!file) return;
  const base = `/api/public/s/${encodeURIComponent(SHARE_TOKEN)}`;
  const box = $("#share-preview-content");
  const dl = $("#share-preview-download");
  const mime = (file.mime || "").toLowerCase();
  const name = file.name || "file";
  if (mime.startsWith("image/") || /\.(jpe?g|png|gif|webp)$/i.test(name)) {
    box.innerHTML = `<img src="${base}/preview/${messageId}" alt="">`;
  } else if (mime.startsWith("video/") || /\.(mp4|mov|webm)$/i.test(name)) {
    box.innerHTML = `<video src="${base}/preview/${messageId}" controls playsinline></video>`;
  } else if (mime === "application/pdf" || name.toLowerCase().endsWith(".pdf")) {
    box.innerHTML = `<iframe src="${base}/preview/${messageId}" title="PDF"></iframe>`;
  } else {
    box.innerHTML = `<p class="hint">Pratinjau tidak tersedia untuk tipe file ini.</p>`;
  }
  if (shareMeta?.allows_download) {
    dl.href = `${base}/download/${messageId}`;
    show(dl);
  } else {
    hide(dl);
  }
  $("#modal-share-preview")?.classList.remove("hidden");
  requestAnimationFrame(() => $("#modal-share-preview")?.classList.add("is-open"));
}

function closeSharePreview() {
  const m = $("#modal-share-preview");
  if (!m) return;
  m.classList.remove("is-open");
  setTimeout(() => m.classList.add("hidden"), 200);
}

async function bootstrap() {
  if (!SHARE_TOKEN) {
    setState("error");
    $("#share-error-state").textContent = "Link tidak valid.";
    return;
  }
  setState("loading");
  try {
    const meta = await loadShareMeta();
    applyShareMeta(meta);
    if (meta.password_required) {
      setState("password");
      return;
    }
    setState("content");
    sharePage = 1;
    await refreshFiles();
  } catch (e) {
    if (e.status === 410) {
      setState("expired");
      return;
    }
    if (e.status === 404) {
      setState("expired");
      return;
    }
    setState("error");
    $("#share-error-state").textContent = e.message || "Gagal memuat link.";
  }
}

$("#form-share-password")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const errEl = $("#share-password-error");
  hide(errEl);
  const pw = $("#share-password-input")?.value || "";
  try {
    await tryUnlock(pw);
    const meta = await loadShareMeta();
    applyShareMeta(meta);
    setState("content");
    sharePage = 1;
    await refreshFiles();
  } catch (err) {
    errEl.textContent =
      err.code === "share_password_invalid" ? "Password salah." : err.message || "Gagal membuka link.";
    show(errEl);
  }
});

document.querySelectorAll("[data-close='modal-share-preview']").forEach((el) => {
  el.addEventListener("click", closeSharePreview);
});

bootstrap();