const $ = (sel) => document.querySelector(sel);

const SHARE_TOKEN = (() => {
  const m = window.location.pathname.match(/^\/s\/([^/]+)\/?$/);
  return m ? decodeURIComponent(m[1]) : "";
})();

const SHARE_API = `/api/public/s/${encodeURIComponent(SHARE_TOKEN)}`;

const FILE_TYPE_MAP = {
  pdf: { label: "PDF", cls: "pdf" },
  doc: { label: "DOC", cls: "word" },
  docx: { label: "DOC", cls: "word" },
  xls: { label: "XLS", cls: "excel" },
  xlsx: { label: "XLS", cls: "excel" },
  csv: { label: "CSV", cls: "excel" },
  ppt: { label: "PPT", cls: "ppt" },
  pptx: { label: "PPT", cls: "ppt" },
  txt: { label: "TXT", cls: "text" },
  md: { label: "MD", cls: "text" },
  zip: { label: "ZIP", cls: "archive" },
  rar: { label: "RAR", cls: "archive" },
  "7z": { label: "7Z", cls: "archive" },
  mp3: { label: "MP3", cls: "audio" },
  wav: { label: "WAV", cls: "audio" },
  apk: { label: "APK", cls: "app" },
  exe: { label: "EXE", cls: "app" },
};

let shareMeta = null;
let sharePage = 1;
let loadedShareFiles = [];
let lastShareListMeta = { total_pages: 1, files: [] };
const selectedShareIds = new Set();

const VISIBILITY_HINTS = {
  both: "Pengunjung dapat melihat pratinjau dan mengunduh file.",
  download: "Pengunjung dapat mengunduh file (thumbnail foto/video tetap tampil).",
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

function fileExt(name, ext) {
  if (ext) return String(ext).toLowerCase();
  const i = String(name || "").lastIndexOf(".");
  return i >= 0 ? name.slice(i + 1).toLowerCase() : "";
}

function fileTypeInfo(file) {
  const ext = fileExt(file.name, file.ext);
  const mime = (file.mime || "").toLowerCase();
  if (FILE_TYPE_MAP[ext]) return FILE_TYPE_MAP[ext];
  if (mime.startsWith("image/")) return { label: "IMG", cls: "image" };
  if (mime.startsWith("video/")) return { label: "VID", cls: "video" };
  if (mime.startsWith("audio/")) return { label: "AUD", cls: "audio" };
  if (mime.includes("pdf")) return { label: "PDF", cls: "pdf" };
  return { label: ext ? ext.toUpperCase().slice(0, 4) : "FILE", cls: "default" };
}

function fileTypeBadgeHtml(file) {
  const t = fileTypeInfo(file);
  return `<div class="ftype ftype-${t.cls}" title="${escapeHtml(file.name)}">${escapeHtml(t.label)}</div>`;
}

function isPdfFile(file) {
  const mime = (file.mime || "").toLowerCase();
  const ext = fileExt(file.name, file.ext);
  return mime === "application/pdf" || ext === "pdf" || /\.pdf$/i.test(file.name || "");
}

function isVideoFile(file) {
  const mime = (file.mime || "").toLowerCase();
  const ext = fileExt(file.name, file.ext);
  if (mime.startsWith("video/") && mime !== "application/octet-stream") return true;
  if (file.kind === "video") return true;
  return /\.(mov|mp4|m4v|webm|mkv|avi|3gp)$/i.test(file.name || "") || ["mov", "mp4", "webm", "mkv", "avi"].includes(ext);
}

function isImageFile(file) {
  const mime = (file.mime || "").toLowerCase();
  if (mime.startsWith("image/")) return true;
  if (file.kind === "image") return true;
  return /\.(jpe?g|png|gif|webp|heic)$/i.test(file.name || "");
}

function canShowMediaThumb(file) {
  if (!shareMeta) return false;
  if (shareMeta.allows_preview && file.previewable) return true;
  if (shareMeta.allows_download && (isImageFile(file) || isVideoFile(file))) return true;
  return false;
}

function canOpenPreviewModal(file) {
  if (!shareMeta) return false;
  if (shareMeta.allows_preview && file.previewable) return true;
  if (shareMeta.allows_download && (isImageFile(file) || isVideoFile(file) || isPdfFile(file))) {
    return true;
  }
  return false;
}

function sharePreviewUrl(messageId) {
  return `${SHARE_API}/preview/${messageId}`;
}

function shareDownloadUrl(messageId) {
  return `${SHARE_API}/download/${messageId}`;
}

function renderShareThumb(file) {
  if (isPdfFile(file) && canOpenPreviewModal(file)) {
    return `<button type="button" class="file-thumb file-thumb-pdf" data-preview-id="${file.id}" title="${escapeHtml(file.name)}">
      ${fileTypeBadgeHtml(file)}
    </button>`;
  }
  if (canShowMediaThumb(file) && isVideoFile(file)) {
    return `<button type="button" class="file-thumb file-thumb-video" data-preview-id="${file.id}" title="${escapeHtml(file.name)}">
      <video src="${sharePreviewUrl(file.id)}" muted preload="metadata"></video>
      <span class="play-badge">▶</span>
    </button>`;
  }
  if (canShowMediaThumb(file) && isImageFile(file)) {
    return `<button type="button" class="file-thumb file-thumb-img" data-preview-id="${file.id}" title="${escapeHtml(file.name)}">
      <img src="${sharePreviewUrl(file.id)}" alt="" loading="lazy">
    </button>`;
  }
  return `<div class="file-thumb file-thumb-icon">${fileTypeBadgeHtml(file)}</div>`;
}

function renderShareCardName(file) {
  const title = escapeHtml(file.name);
  if (canOpenPreviewModal(file)) {
    return `<button type="button" class="file-card-name file-card-name-link" data-preview-id="${file.id}" title="${title}">${title}</button>`;
  }
  return `<div class="file-card-name" title="${title}">${title}</div>`;
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

function updateShareBulkBar() {
  const bar = $("#share-bulk-bar");
  const countEl = $("#share-bulk-count");
  const selectAll = $("#share-select-all");
  if (!bar || !shareMeta?.allows_download) {
    hide(bar);
    return;
  }
  show(bar);
  const n = selectedShareIds.size;
  if (countEl) countEl.textContent = n ? `${n} dipilih` : "Pilih file untuk diunduh";
  if (selectAll && loadedShareFiles.length) {
    const allOnPage = loadedShareFiles.every((f) => selectedShareIds.has(f.id));
    selectAll.checked = allOnPage;
    selectAll.indeterminate =
      !allOnPage && loadedShareFiles.some((f) => selectedShareIds.has(f.id));
  }
}

function toggleShareSelection(id, checked) {
  if (checked) selectedShareIds.add(id);
  else selectedShareIds.delete(id);
  updateShareBulkBar();
}

async function loadShareMeta() {
  return apiPublic(SHARE_API);
}

function applyShareMeta(meta) {
  shareMeta = meta;
  const title = meta.title || (meta.share_type === "file" ? "File dibagikan" : "Folder dibagikan");
  $("#share-title").textContent = title;
  $("#share-password-title").textContent = title;
  $("#share-visibility-hint").textContent = VISIBILITY_HINTS[meta.visibility] || "";
}

async function tryUnlock(password) {
  return apiPublic(`${SHARE_API}/unlock`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
}

async function loadFiles(page = 1) {
  const q = new URLSearchParams({ page: String(page), per_page: "24" });
  return apiPublic(`${SHARE_API}/files?${q}`);
}

function bindSharePreviewHandlers(files) {
  const grid = $("#share-files-grid");
  if (!grid) return;
  const open = (id) => {
    const file = files.find((x) => x.id === id);
    if (file) openSharePreview(file);
  };
  grid.querySelectorAll("[data-preview-id]").forEach((el) => {
    el.onclick = (e) => {
      e.preventDefault();
      open(parseInt(el.dataset.previewId, 10));
    };
  });
}

function renderShareFiles(data, page) {
  const grid = $("#share-files-grid");
  const empty = $("#share-files-empty");
  lastShareListMeta = data;
  const files = data.files || [];
  loadedShareFiles = files;

  if (!files.length) {
    hide(grid);
    show(empty);
    empty.textContent = "Tidak ada file di folder ini.";
    updateShareBulkBar();
    return;
  }
  show(grid);
  hide(empty);
  grid.innerHTML = "";

  const canDownload = shareMeta?.allows_download;

  files.forEach((f) => {
    const checked = selectedShareIds.has(f.id);
    const card = document.createElement("article");
    card.className = "file-card" + (checked ? " selected" : "");
    let actions = "";
    if (canDownload) {
      actions += `<a class="btn ghost sm" href="${shareDownloadUrl(f.id)}" download>Download</a>`;
    }
    if (!actions && !canOpenPreviewModal(f)) {
      actions = `<span class="hint">Akses terbatas</span>`;
    }
    const checkHtml = canDownload
      ? `<label class="file-check">
          <input type="checkbox" class="share-file-select" data-id="${f.id}" ${checked ? "checked" : ""}>
        </label>`
      : `<div class="file-check" aria-hidden="true"></div>`;

    card.innerHTML = `
      ${checkHtml}
      ${renderShareThumb(f)}
      <div class="file-card-body">
        ${renderShareCardName(f)}
        <div class="file-card-meta">
          <span>${escapeHtml(f.sizeStr || formatSize(f.size))}</span>
        </div>
      </div>
      ${actions ? `<div class="file-card-actions">${actions}</div>` : ""}`;

    const cb = card.querySelector(".share-file-select");
    if (cb) {
      cb.onchange = () => {
        toggleShareSelection(f.id, cb.checked);
        card.classList.toggle("selected", cb.checked);
      };
    }
    grid.appendChild(card);
  });

  bindSharePreviewHandlers(files);
  updateShareBulkBar();

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
    renderShareFiles(data, sharePage);
  } catch (e) {
    if (e.code === "share_password_required") {
      setState("password");
      return;
    }
    throw e;
  }
}

async function downloadSelectedShare() {
  const ids = [...selectedShareIds];
  if (!ids.length) {
    alert("Pilih minimal satu file.");
    return;
  }
  const btn = $("#share-bulk-download");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Menyiapkan ZIP…";
  }
  try {
    if (ids.length === 1) {
      window.location.href = shareDownloadUrl(ids[0]);
      return;
    }
    const r = await fetch(`${SHARE_API}/download/bulk`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message_ids: ids }),
    });
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      throw new Error(data.detail || r.statusText);
    }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `share-${ids.length}-files.zip`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    alert(e.message || "Gagal mengunduh.");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Download terpilih";
    }
  }
}

function openSharePreview(file) {
  if (!canOpenPreviewModal(file)) return;
  const box = $("#share-preview-content");
  const dl = $("#share-preview-download");
  const mime = (file.mime || "").toLowerCase();
  const name = file.name || "file";
  const pid = file.id;

  if (isImageFile(file)) {
    box.innerHTML = `<img src="${sharePreviewUrl(pid)}" alt="">`;
  } else if (isVideoFile(file)) {
    box.innerHTML = `<video src="${sharePreviewUrl(pid)}" controls playsinline></video>`;
  } else if (isPdfFile(file)) {
    box.innerHTML = `<iframe src="${sharePreviewUrl(pid)}" title="PDF"></iframe>`;
  } else {
    box.innerHTML = `<p class="hint">Pratinjau tidak tersedia.</p>`;
  }

  if (shareMeta?.allows_download) {
    dl.href = shareDownloadUrl(pid);
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
  setTimeout(() => {
    m.classList.add("hidden");
    const v = $("#share-preview-content")?.querySelector("video");
    if (v) v.pause();
  }, 200);
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
    selectedShareIds.clear();
    await refreshFiles();
  } catch (e) {
    if (e.status === 410 || e.status === 404) {
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
    selectedShareIds.clear();
    await refreshFiles();
  } catch (err) {
    errEl.textContent =
      err.code === "share_password_invalid" ? "Password salah." : err.message || "Gagal membuka link.";
    show(errEl);
  }
});

$("#share-select-all")?.addEventListener("change", (e) => {
  const checked = e.target.checked;
  loadedShareFiles.forEach((f) => {
    if (checked) selectedShareIds.add(f.id);
    else selectedShareIds.delete(f.id);
  });
  renderShareFiles(lastShareListMeta, sharePage);
});

$("#share-bulk-clear")?.addEventListener("click", () => {
  selectedShareIds.clear();
  renderShareFiles(lastShareListMeta, sharePage);
});

$("#share-bulk-download")?.addEventListener("click", downloadSelectedShare);

document.querySelectorAll("[data-close='modal-share-preview']").forEach((el) => {
  el.addEventListener("click", closeSharePreview);
});

bootstrap();