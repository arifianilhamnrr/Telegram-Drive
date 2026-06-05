const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

let config = { gate_enabled: false, registration_enabled: true, max_upload_mb: 2000, ytdlp_available: false };
let accountUsername = "";
let accountIsAdmin = false;
let accountRegisterMode = false;
let currentAppPanel = "drive";
let currentFolderId = 0;
let currentFolderName = "Saved Messages";
let pendingUploadFiles = [];
let uploadPreviewUrls = [];
let confirmCallback = null;
let confirmSuppressModals = [];
let filesLoadGen = 0;
let filesFetchAbort = null;

function normalizeFolderId(id) {
  const n = Number(id);
  return Number.isFinite(n) ? n : 0;
}

function sameFolderId(a, b) {
  return normalizeFolderId(a) === normalizeFolderId(b);
}
let selectedIds = new Set();
let loadedFiles = [];
let filesFilter = "all";
let filesSearch = "";
let filesPage = 1;
const FILES_PER_PAGE = 24;
let filesListMeta = { total: 0, total_pages: 1, page: 1, filter: "all", q: "" };
let filesSearchTimer = null;
let pdfPreviewTask = null;
let pdfRenderGen = 0;
const MOTION_MS = 240;
const SAWERIA_URL_DEFAULT = "https://saweria.co/arifianilhamnr";
const DONATION_QR_URL = "/api/donation/qr";
let donationInfo = { enabled: true, saweria_url: SAWERIA_URL_DEFAULT, qr_available: true };
let donateQrBound = false;

function applyDonationInfo(info) {
  if (!info || typeof info !== "object") return;
  donationInfo = {
    enabled: info.enabled !== false,
    saweria_url: (info.saweria_url || "").trim() || SAWERIA_URL_DEFAULT,
    qr_available: info.qr_available !== false,
  };
}

function setAdminSectionVisible(el, visible) {
  if (!el) return;
  if (visible) {
    show(el);
    el.hidden = false;
    el.setAttribute("aria-hidden", "false");
  } else {
    hide(el);
    el.hidden = true;
    el.setAttribute("aria-hidden", "true");
  }
}

function updateNotifyDonateBlock() {
  const block = $(".notify-donate");
  if (!block) return;
  if (!donationInfo.enabled) {
    hide(block);
    return;
  }
  show(block);
  const qrWrap = $(".notify-donate-qr-wrap");
  if (qrWrap) qrWrap.classList.toggle("hidden", !donationInfo.qr_available);
  const link = $(".notify-donate-link");
  if (link) {
    link.href = donationInfo.saweria_url;
    try {
      const u = new URL(donationInfo.saweria_url);
      link.textContent = u.hostname + u.pathname.replace(/\/$/, "") || donationInfo.saweria_url;
    } catch {
      link.textContent = donationInfo.saweria_url;
    }
  }
}


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
  rtf: { label: "RTF", cls: "text" },
  json: { label: "JSON", cls: "code" },
  xml: { label: "XML", cls: "code" },
  html: { label: "HTML", cls: "code" },
  htm: { label: "HTML", cls: "code" },
  css: { label: "CSS", cls: "code" },
  js: { label: "JS", cls: "code" },
  ts: { label: "TS", cls: "code" },
  py: { label: "PY", cls: "code" },
  java: { label: "JAVA", cls: "code" },
  cpp: { label: "C++", cls: "code" },
  c: { label: "C", cls: "code" },
  zip: { label: "ZIP", cls: "archive" },
  rar: { label: "RAR", cls: "archive" },
  "7z": { label: "7Z", cls: "archive" },
  tar: { label: "TAR", cls: "archive" },
  gz: { label: "GZ", cls: "archive" },
  apk: { label: "APK", cls: "app" },
  exe: { label: "EXE", cls: "app" },
  dmg: { label: "DMG", cls: "app" },
  mp3: { label: "MP3", cls: "audio" },
  wav: { label: "WAV", cls: "audio" },
  flac: { label: "FLAC", cls: "audio" },
  ogg: { label: "OGG", cls: "audio" },
  m4a: { label: "M4A", cls: "audio" },
  mp4: { label: "MP4", cls: "video" },
  webm: { label: "WEBM", cls: "video" },
  mov: { label: "MOV", cls: "video" },
  mkv: { label: "MKV", cls: "video" },
  avi: { label: "AVI", cls: "video" },
  jpg: { label: "JPG", cls: "image" },
  jpeg: { label: "JPG", cls: "image" },
  png: { label: "PNG", cls: "image" },
  gif: { label: "GIF", cls: "image" },
  webp: { label: "WEBP", cls: "image" },
  svg: { label: "SVG", cls: "image" },
};

const THEME_STORAGE_KEY = "td-theme";
const THEME_OPTIONS = [
  { id: "default", name: "Default", desc: "Gelap biru — bawaan" },
  { id: "retro", name: "Retro", desc: "Grunge 90s — mixtape & zine" },
  { id: "glass", name: "Glassmorphism", desc: "Kaca buram — gelap netral" },
  { id: "ocean", name: "Ocean Teal", desc: "Gelap dengan aksen teal" },
  { id: "dusk", name: "Dusk Purple", desc: "Ungu lembut malam hari" },
  { id: "light", name: "Light", desc: "Terang bersih siang hari" },
];

let floodWaitTimer = null;

function parseApiDetail(data) {
  const d = data?.detail;
  if (d && typeof d === "object" && d.code === "flood_wait") {
    return {
      type: "flood_wait",
      seconds: Number(d.seconds) || 60,
      message: d.message || "Telegram meminta Anda menunggu sebentar.",
    };
  }
  return null;
}

function apiErrorMessage(data, fallback) {
  const parsed = parseApiDetail(data);
  if (parsed) return parsed.message;
  const d = data?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((x) => x.msg || x.message || JSON.stringify(x)).join("; ");
  return data?.error || fallback;
}

function formatWaitTime(seconds) {
  const sec = Math.max(1, Math.ceil(Number(seconds) || 1));
  if (sec < 60) return `${sec} detik`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return s ? `${m} menit ${s} detik` : `${m} menit`;
}

function showFloodWaitModal(seconds, message) {
  const modal = $("#modal-flood-wait");
  const msgEl = $("#flood-wait-message");
  const countEl = $("#flood-wait-countdown");
  if (!modal || !msgEl) return;

  let left = Math.max(1, Math.ceil(Number(seconds) || 60));
  msgEl.textContent = message || "Telegram membatasi permintaan terlalu cepat.";
  if (countEl) countEl.textContent = formatWaitTime(left);

  if (floodWaitTimer) clearInterval(floodWaitTimer);
  floodWaitTimer = setInterval(() => {
    left -= 1;
    if (countEl) countEl.textContent = left > 0 ? formatWaitTime(left) : "Bisa dicoba lagi";
    if (left <= 0) {
      clearInterval(floodWaitTimer);
      floodWaitTimer = null;
    }
  }, 1000);

  openModal("modal-flood-wait");
}

function closeFloodWaitModal() {
  if (floodWaitTimer) {
    clearInterval(floodWaitTimer);
    floodWaitTimer = null;
  }
  closeModal("modal-flood-wait");
}

function handleApiError(data, fallback) {
  const parsed = parseApiDetail(data);
  if (parsed?.type === "flood_wait") {
    showFloodWaitModal(parsed.seconds, parsed.message);
    return parsed.message;
  }
  return apiErrorMessage(data, fallback);
}

function isFloodWaitError(err) {
  return err && err.code === "flood_wait";
}

function throwApiError(data, fallback) {
  const parsed = parseApiDetail(data);
  const msg = handleApiError(data, fallback);
  routeSessionError(msg, data?.detail);
  const err = new Error(msg);
  if (parsed?.type === "flood_wait") err.code = "flood_wait";
  throw err;
}

function routeSessionError(msg, detail) {
  const code = typeof detail === "string" ? detail : msg;
  if (code === "account_required") {
    showAccountView(config.registration_enabled !== false);
  } else if (code === "telegram_required" || code === "not_authenticated") {
    showView("auth");
    setAuthStep(0);
  }
}

function applyTheme(themeId) {
  const id = THEME_OPTIONS.some((t) => t.id === themeId) ? themeId : "default";
  document.documentElement.setAttribute("data-theme", id);
  try {
    localStorage.setItem(THEME_STORAGE_KEY, id);
  } catch {
    /* ignore */
  }
  $$(".theme-option").forEach((el) => {
    el.classList.toggle("active", el.dataset.theme === id);
  });
}

function initTheme() {
  let saved = "default";
  try {
    saved = localStorage.getItem(THEME_STORAGE_KEY) || "default";
  } catch {
    /* ignore */
  }
  applyTheme(saved);
}

function renderThemePicker() {
  const box = $("#theme-picker");
  if (!box) return;
  box.innerHTML = "";
  THEME_OPTIONS.forEach((t) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "theme-option";
    btn.dataset.theme = t.id;
    btn.innerHTML = `
      <span class="theme-swatch theme-swatch-${t.id}"></span>
      <span class="theme-option-text">
        <strong>${escapeHtml(t.name)}</strong>
        <span class="hint">${escapeHtml(t.desc)}</span>
      </span>`;
    btn.onclick = () => applyTheme(t.id);
    box.appendChild(btn);
  });
  applyTheme(document.documentElement.getAttribute("data-theme") || "default");
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : {},
    ...opts,
    body: opts.body && !(opts.body instanceof FormData) ? JSON.stringify(opts.body) : opts.body,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 404 && path.includes("/api/account/")) {
      throw new Error("Fitur akun belum aktif di server — jalankan: bash update.sh");
    }
    throwApiError(data, res.statusText);
  }
  return data;
}

function setBtnLoading(btn, loading, loadingText) {
  if (!btn) return;
  if (!btn.dataset.origLabel) btn.dataset.origLabel = btn.textContent;
  btn.disabled = loading;
  btn.textContent = loading ? loadingText : btn.dataset.origLabel;
}

function getMaxUploadBytes() {
  const mb = Number(config.max_upload_mb) || 2000;
  return mb * 1024 * 1024;
}

const YTDLP_HOST_SUFFIXES = [
  "youtube.com",
  "youtu.be",
  "music.youtube.com",
  "tiktok.com",
  "instagram.com",
  "twitter.com",
  "x.com",
  "facebook.com",
  "fb.watch",
  "vimeo.com",
];

function isYtdlpUrl(url) {
  try {
    const host = new URL(url).hostname.toLowerCase().replace(/^www\./, "");
    return YTDLP_HOST_SUFFIXES.some((s) => host === s || host.endsWith(`.${s}`));
  } catch {
    return false;
  }
}

function applyUploadLimitHints() {
  const mb = Number(config.max_upload_mb) || 2000;
  const text = `Maksimal ${mb} MB per file (batas server).`;
  const hint = $("#upload-limit-hint");
  const importHint = $("#import-limit-hint");
  if (hint) hint.textContent = text;
  if (importHint) {
    importHint.textContent = `Unduhan dari link: maksimal ${mb} MB per file.`;
  }
}

function validatePendingUploadFiles(files) {
  const btn = $("#btn-upload");
  const warn = $("#upload-size-warn");
  const list = files || [];
  if (!list.length) {
    if (btn && !btn.dataset.loading) btn.disabled = false;
    hide(warn);
    return true;
  }
  const max = getMaxUploadBytes();
  const bad = list.filter((f) => f.size > max);
  if (bad.length) {
    if (btn) btn.disabled = true;
    if (warn) {
      const names = bad
        .slice(0, 3)
        .map((f) => f.name)
        .join(", ");
      warn.textContent = `${bad.length} file melebihi batas ${formatSize(max)}: ${names}${bad.length > 3 ? "…" : ""}`;
      show(warn);
    }
    return false;
  }
  if (btn && !btn.dataset.loading) btn.disabled = false;
  hide(warn);
  return true;
}

function showTransferLoader(title, detail = "") {
  const modal = $("#modal-transfer");
  if (!modal) return;
  $("#transfer-title").textContent = title;
  updateTransferProgress(0, detail || "Mohon tunggu…");
  modal.classList.add("modal-top");
  openModal("modal-transfer");
}

function updateTransferProgress(percent, detail, phase) {
  const bar = $("#transfer-progress-bar");
  const pctEl = $("#transfer-percent");
  const wrap = bar?.parentElement;
  if (detail) $("#transfer-detail").textContent = detail;
  if (percent == null || Number.isNaN(percent)) {
    wrap?.classList.add("indeterminate");
    if (bar) bar.style.width = "";
    if (pctEl) pctEl.textContent = phase === "telegram" ? "Mengunggah…" : "Mengunduh…";
    return;
  }
  wrap?.classList.remove("indeterminate");
  const p = Math.max(0, Math.min(100, Math.round(percent)));
  if (bar) bar.style.width = `${p}%`;
  if (pctEl) pctEl.textContent = `${p}%`;
}

function hideTransferLoader() {
  const modal = $("#modal-transfer");
  if (!modal) return;
  closeModal("modal-transfer");
  modal.classList.remove("modal-top");
  $("#transfer-progress-bar")?.parentElement?.classList.remove("indeterminate");
}

function transferPercentFromProgress(loaded, total, phase) {
  if (phase === "telegram") {
    const t = total || loaded || 1;
    return 88 + Math.min(11, Math.round((loaded / t) * 11));
  }
  if (total && total > 0) {
    return Math.min(85, Math.round((loaded / total) * 85));
  }
  return null;
}

function transferDetailFromProgress(loaded, total, phase, message) {
  if (message) return message;
  if (phase === "telegram") return "Mengunggah ke Telegram…";
  if (total && total > 0) {
    const pct = Math.round((loaded / total) * 100);
    return `Mengunduh… ${formatSize(loaded)} / ${formatSize(total)} (${pct}%)`;
  }
  return `Mengunduh… ${formatSize(loaded)}`;
}

function xhrPostForm(url, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.withCredentials = true;
    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded, e.total);
    });
    xhr.addEventListener("load", () => {
      let data = {};
      try {
        data = JSON.parse(xhr.responseText || "{}");
      } catch {
        /* ignore */
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(data);
        return;
      }
      const msg = handleApiError(data, xhr.statusText);
      const err = new Error(msg);
      if (parseApiDetail(data)?.type === "flood_wait") err.code = "flood_wait";
      reject(err);
    });
    xhr.addEventListener("error", () => reject(new Error("Jaringan gagal saat upload")));
    xhr.send(formData);
  });
}

async function importUrlWithProgress(body) {
  showTransferLoader("Import dari link", "Memulai unduhan…");
  const res = await fetch("/api/import/url", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    hideTransferLoader();
    const data = await res.json().catch(() => ({}));
    throwApiError(data, res.statusText);
  }
  const reader = res.body?.getReader();
  if (!reader) {
    hideTransferLoader();
    throw new Error("Browser tidak mendukung progress unduhan");
  }
  const decoder = new TextDecoder();
  let buf = "";
  let result = null;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop() || "";
    for (const block of parts) {
      const line = block.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      let payload;
      try {
        payload = JSON.parse(line.slice(6));
      } catch {
        continue;
      }
      if (payload.event === "progress") {
        const pct = transferPercentFromProgress(
          payload.loaded,
          payload.total,
          payload.phase
        );
        const detail = transferDetailFromProgress(
          payload.loaded,
          payload.total,
          payload.phase,
          payload.message
        );
        updateTransferProgress(pct, detail, payload.phase);
      } else if (payload.event === "done") {
        result = payload;
        updateTransferProgress(100, "Selesai.", payload.phase);
      } else if (payload.event === "error") {
        throw new Error(payload.message || "Import gagal");
      }
    }
  }
  if (!result?.ok) throw new Error("Import tidak selesai");
  return result;
}

function show(el) { el.classList.remove("hidden"); }
function hide(el) { el.classList.add("hidden"); }
function showError(box, msg) {
  box.textContent = msg;
  show(box);
}
function hideError(box) {
  hide(box);
  box.textContent = "";
  box.style.color = "";
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fileExt(name, ext) {
  if (ext) return ext.toLowerCase();
  const n = String(name || "");
  const i = n.lastIndexOf(".");
  return i > 0 ? n.slice(i + 1).toLowerCase() : "";
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
  const ext = (file.ext || fileExt(file.name) || "").toLowerCase();
  return mime === "application/pdf" || ext === "pdf" || /\.pdf$/i.test(file.name || "");
}

const VIDEO_MIME_BY_EXT = {
  mov: "video/quicktime",
  qt: "video/quicktime",
  mp4: "video/mp4",
  m4v: "video/mp4",
  webm: "video/webm",
  mkv: "video/x-matroska",
  avi: "video/x-msvideo",
  wmv: "video/x-ms-wmv",
  "3gp": "video/3gpp",
  "3g2": "video/3gpp2",
  ogv: "video/ogg",
};

function isVideoFile(file) {
  if (!file) return false;
  const mime = (file.mime || "").toLowerCase();
  const ext = (file.ext || fileExt(file.name) || "").toLowerCase();
  if (mime.startsWith("video/") && mime !== "application/octet-stream") return true;
  if (file.kind === "video") return true;
  return ext in VIDEO_MIME_BY_EXT || /\.(mov|mp4|m4v|webm|mkv|avi|3gp|3g2)$/i.test(file.name || "");
}

function videoMimeType(file) {
  const mime = (file.mime || "").toLowerCase();
  if (mime.startsWith("video/") && mime !== "application/octet-stream") return mime;
  const ext = (file.ext || fileExt(file.name) || "").toLowerCase();
  return VIDEO_MIME_BY_EXT[ext] || "video/mp4";
}

function isPreviewableFile(file) {
  if (file.previewable) return true;
  if (isPdfFile(file) || isVideoFile(file)) return true;
  const k = file.kind || "";
  const mime = (file.mime || "").toLowerCase();
  return k === "image" || k === "video" || mime.startsWith("image/") || mime.startsWith("video/");
}

function previewUrl(folderId, messageId) {
  return `/api/preview/${folderId}/${messageId}`;
}

function formatSize(bytes) {
  if (!bytes && bytes !== 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  let n = bytes;
  for (const u of ["KB", "MB", "GB"]) {
    n /= 1024;
    if (n < 1024) return `${n.toFixed(1)} ${u}`;
  }
  return `${(n / 1024).toFixed(1)} TB`;
}

function formatDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("id-ID", {
      day: "numeric",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "—";
  }
}

function getOpenModals() {
  return [...$$(".modal")].filter((m) => m.classList.contains("is-open"));
}

function syncModalLayers() {
  const open = getOpenModals();
  open.forEach((m) => m.classList.remove("modal-behind", "modal-top"));
  open.forEach((m, i) => {
    m.style.zIndex = String(400 + i * 10);
  });
  if (open.length > 1) {
    open.slice(0, -1).forEach((m) => m.classList.add("modal-behind"));
    open[open.length - 1].classList.add("modal-top");
  } else if (open.length === 1) {
    open[0].classList.add("modal-top");
  }
}

function openModal(id) {
  const el = $("#" + id);
  if (!el) return;
  el.classList.remove("hidden", "is-closing");
  requestAnimationFrame(() => {
    requestAnimationFrame(() => el.classList.add("is-open"));
  });
  document.body.classList.add("modal-open");
  syncModalLayers();
}

function finishCloseModal(el) {
  if (!el) return;
  el.classList.add("hidden");
  el.classList.remove("is-open", "is-closing", "modal-behind", "modal-top", "modal-suppressed");
  el.style.zIndex = "";
  if (!getOpenModals().length) {
    document.body.classList.remove("modal-open");
  } else {
    syncModalLayers();
  }
}

function closeModal(id) {
  const el = $("#" + id);
  if (!el) return;
  if (el.classList.contains("hidden") && !el.classList.contains("is-open")) return;

  if (id === "modal-preview") {
    clearFilePreviewModal();
  }

  if (!el.classList.contains("is-open")) {
    finishCloseModal(el);
    return;
  }

  el.classList.remove("is-open");
  el.classList.add("is-closing");

  let done = false;
  const complete = () => {
    if (done) return;
    done = true;
    finishCloseModal(el);
  };

  const panel = el.querySelector(".modal-panel");
  if (panel) {
    panel.addEventListener("transitionend", complete, { once: true });
  }
  setTimeout(complete, MOTION_MS + 80);
}

function clearFilePreviewModal() {
  pdfRenderGen += 1;
  if (pdfPreviewTask) {
    try {
      pdfPreviewTask.destroy();
    } catch (_) {
      /* ignore */
    }
    pdfPreviewTask = null;
  }
  const box = $("#modal-preview-content");
  if (box) {
    box.innerHTML = "";
    box.className = "modal-preview-content";
  }
  const body = $("#modal-preview-body");
  body?.classList.remove("is-pdf");
  hide($("#modal-preview-header"));
  const closeMedia = document.querySelector(".modal-close-media");
  closeMedia?.classList.remove("hidden");
  $("#modal-preview")?.classList.remove("modal-pdf-open");
  document.body.classList.remove("pdf-preview-open");
}

function loadScriptOnce(src, isReady) {
  return new Promise((resolve, reject) => {
    if (isReady()) {
      resolve();
      return;
    }
    const existing = document.querySelector(`script[data-src-key="${src}"]`);
    if (existing) {
      if (isReady()) {
        resolve();
        return;
      }
      existing.addEventListener("load", () => resolve(), { once: true });
      existing.addEventListener("error", () => reject(new Error("Gagal memuat skrip")), { once: true });
      return;
    }
    const s = document.createElement("script");
    s.src = src;
    s.dataset.srcKey = src;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error("Gagal memuat PDF viewer"));
    document.head.appendChild(s);
  });
}

const PDFJS_CDN = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174";

async function ensurePdfJs() {
  await loadScriptOnce(`${PDFJS_CDN}/pdf.min.js`, () => !!window.pdfjsLib);
  window.pdfjsLib.GlobalWorkerOptions.workerSrc = `${PDFJS_CDN}/pdf.worker.min.js`;
  return window.pdfjsLib;
}

async function renderPdfInViewer(url, box) {
  const gen = ++pdfRenderGen;
  box.className = "modal-preview-content is-pdf-viewer";
  box.innerHTML = '<p class="pdf-loading hint">Memuat PDF…</p>';

  try {
    const pdfjs = await ensurePdfJs();
    if (gen !== pdfRenderGen) return;

    if (pdfPreviewTask) {
      try {
        pdfPreviewTask.destroy();
      } catch (_) {
        /* ignore */
      }
      pdfPreviewTask = null;
    }

    const task = pdfjs.getDocument({ url, withCredentials: true });
    pdfPreviewTask = task;
    const pdf = await task.promise;
    if (gen !== pdfRenderGen) return;

    const pagesEl = document.createElement("div");
    pagesEl.className = "pdf-pages";
    box.innerHTML = "";
    box.appendChild(pagesEl);

    const pad = 12;
    const containerWidth = Math.max(240, (box.clientWidth || box.parentElement?.clientWidth || 360) - pad);

    for (let num = 1; num <= pdf.numPages; num += 1) {
      if (gen !== pdfRenderGen) return;
      const page = await pdf.getPage(num);
      const baseViewport = page.getViewport({ scale: 1 });
      const scale = containerWidth / baseViewport.width;
      const viewport = page.getViewport({ scale });
      const canvas = document.createElement("canvas");
      canvas.className = "pdf-page-canvas";
      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      canvas.setAttribute("data-page", String(num));
      const ctx = canvas.getContext("2d");
      await page.render({ canvasContext: ctx, viewport }).promise;
      const wrap = document.createElement("div");
      wrap.className = "pdf-page-wrap";
      wrap.appendChild(canvas);
      pagesEl.appendChild(wrap);
    }
  } catch (err) {
    if (gen !== pdfRenderGen) return;
    box.innerHTML = `<p class="error">Gagal memuat PDF: ${escapeHtml(err.message || "unknown")}</p>
      <p class="hint">Coba <a href="${url}" target="_blank" rel="noopener">buka di tab baru</a>.</p>`;
  }
}

function closeAllModals() {
  $$(".modal").forEach((m) => {
    finishCloseModal(m);
  });
  confirmSuppressModals = [];
}

function finishConfirm(result) {
  const cb = confirmCallback;
  confirmCallback = null;
  confirmSuppressModals.forEach((m) => m.classList.remove("modal-suppressed"));
  confirmSuppressModals = [];
  closeModal("modal-confirm");
  if (cb) cb(result);
}

function showConfirm({ title, message, extraHtml, okLabel = "Ya, lanjutkan", danger = false }) {
  return new Promise((resolve) => {
    confirmCallback = resolve;
    confirmSuppressModals = getOpenModals().filter((m) => m.id !== "modal-confirm");
    confirmSuppressModals.forEach((m) => m.classList.add("modal-suppressed"));
    $("#confirm-title").textContent = title;
    $("#confirm-message").textContent = message;
    const extra = $("#confirm-extra");
    if (extraHtml) {
      extra.innerHTML = extraHtml;
      show(extra);
    } else {
      extra.innerHTML = "";
      hide(extra);
    }
    const okBtn = $("#confirm-ok");
    okBtn.textContent = okLabel;
    okBtn.classList.toggle("danger", danger);
    openModal("modal-confirm");
  });
}

$("#confirm-ok")?.addEventListener("click", () => finishConfirm(true));

$$("[data-close]").forEach((el) => {
  el.addEventListener("click", () => {
    const id = el.dataset.close;
    if (id === "modal-confirm" && confirmCallback) {
      finishConfirm(false);
      return;
    }
    closeModal(id);
  });
});

function openFilePreview(folderId, file) {
  const box = $("#modal-preview-content");
  const cap = $("#modal-preview-caption");
  const body = $("#modal-preview-body");
  const header = $("#modal-preview-header");
  const dl = $("#modal-preview-download");
  const tab = $("#modal-preview-open-tab");
  const closeMedia = document.querySelector(".modal-close-media");
  const modal = $("#modal-preview");
  if (!box || !cap) return;

  clearFilePreviewModal();
  cap.textContent = file.name;
  const url = previewUrl(folderId, file.id);
  const mime = (file.mime || "").toLowerCase();

  if (isPdfFile(file)) {
    body?.classList.add("is-pdf");
    modal?.classList.add("modal-pdf-open");
    show(header);
    closeMedia?.classList.add("hidden");
    if (dl) {
      dl.href = `/api/download/${folderId}/${file.id}`;
      dl.setAttribute("download", file.name || "document.pdf");
    }
    if (tab) tab.href = url;
    document.body.classList.add("pdf-preview-open");
    openModal("modal-preview");
    requestAnimationFrame(() => {
      requestAnimationFrame(() => renderPdfInViewer(url, box));
    });
    return;
  }

  body?.classList.remove("is-pdf");
  hide(header);
  closeMedia?.classList.remove("hidden");
  box.className = "modal-preview-content modal-preview-media";
  if (isVideoFile(file)) {
    const vtype = escapeHtml(videoMimeType(file));
    const dlUrl = `/api/download/${folderId}/${file.id}`;
    box.innerHTML = `
      <div class="video-preview-wrap">
        <video
          id="modal-preview-video"
          class="modal-preview-video"
          controls
          playsinline
          webkit-playsinline="true"
          preload="auto"
          controlsList="nodownload"
        >
          <source src="${url}" type="${vtype}">
        </video>
        <p class="hint video-preview-hint hidden" id="video-preview-error">
          Video tidak bisa diputar di browser ini.
          <a href="${dlUrl}" download>Download file</a>
        </p>
      </div>`;
    const vid = box.querySelector("#modal-preview-video");
    const errHint = box.querySelector("#video-preview-error");
    if (vid) {
      const showVideoError = () => {
        if (errHint) show(errHint);
      };
      vid.addEventListener("error", showVideoError);
      vid.addEventListener("loadedmetadata", () => {
        if (errHint) hide(errHint);
      });
      try {
        vid.load();
      } catch (_) {
        showVideoError();
      }
    }
  } else {
    box.innerHTML = `<img src="${url}" alt="${escapeHtml(file.name)}" loading="lazy">`;
  }
  openModal("modal-preview");
}

function clearUploadPreview() {
  uploadPreviewUrls.forEach((u) => URL.revokeObjectURL(u));
  uploadPreviewUrls = [];
  pendingUploadFiles = [];
  $("#upload-preview").innerHTML = "";
  hide($("#upload-preview"));
  show($("#dropzone-inner"));
  validatePendingUploadFiles([]);
}

function renderLocalUploadPreview(fileList) {
  clearUploadPreview();
  const files = [...fileList];
  if (!files.length) return;
  pendingUploadFiles = files;
  validatePendingUploadFiles(files);
  const maxBytes = getMaxUploadBytes();
  const box = $("#upload-preview");
  hide($("#dropzone-inner"));
  show(box);
  const items = files
    .map((file) => {
      const mime = (file.type || "").toLowerCase();
      let media = fileTypeBadgeHtml({ name: file.name, ext: fileExt(file.name), mime: file.type });
      if (mime.startsWith("image/")) {
        const url = URL.createObjectURL(file);
        uploadPreviewUrls.push(url);
        media = `<img src="${url}" alt="" class="upload-preview-img">`;
      } else if (mime.startsWith("video/")) {
        const url = URL.createObjectURL(file);
        uploadPreviewUrls.push(url);
        media = `<video src="${url}" class="upload-preview-vid" muted></video>`;
      }
      const over = file.size > maxBytes;
      return `<div class="upload-preview-card${over ? " upload-preview-over" : ""}">
        <div class="upload-preview-media">${media}</div>
        <div>
          <div class="upload-preview-name">${escapeHtml(file.name)}</div>
          <div class="hint" style="${over ? "color:var(--danger)" : ""}">${formatSize(file.size)}${over ? " — melebihi batas" : ""}</div>
        </div>
      </div>`;
    })
    .join("");
  box.innerHTML = `<div class="upload-preview-list">${items}</div>
    <p class="hint" style="margin-top:0.5rem">${files.length} file siap diupload</p>`;
}

function renderFileCardName(file) {
  const title = escapeHtml(file.name);
  if (isPreviewableFile(file)) {
    return `<button type="button" class="file-card-name file-card-name-link" data-preview-id="${file.id}" title="${title}">${title}</button>`;
  }
  return `<div class="file-card-name" title="${title}">${title}</div>`;
}

function renderFileThumb(folderId, file) {
  if (isPdfFile(file)) {
    return `<button type="button" class="file-thumb file-thumb-pdf" data-preview-id="${file.id}" title="${escapeHtml(file.name)}">
      ${fileTypeBadgeHtml(file)}
    </button>`;
  }
  if (isPreviewableFile(file)) {
    const url = previewUrl(folderId, file.id);
    if (isVideoFile(file)) {
      return `<div class="file-thumb file-thumb-video" data-preview-id="${file.id}">
        <video src="${url}" muted preload="metadata"></video>
        <span class="play-badge">▶</span>
      </div>`;
    }
    return `<button type="button" class="file-thumb file-thumb-img" data-preview-id="${file.id}">
      <img src="${url}" alt="" loading="lazy">
    </button>`;
  }
  return `<div class="file-thumb file-thumb-icon">${fileTypeBadgeHtml(file)}</div>`;
}

function setAuthStep(n) {
  $$(".step-dot").forEach((d, i) => {
    d.classList.toggle("active", i === n);
    d.classList.toggle("done", i < n);
  });
  hide($("#auth-setup"));
  hide($("#auth-phone"));
  hide($("#auth-code"));
  hide($("#auth-2fa"));
  const panels = ["#auth-setup", "#auth-phone", "#auth-code", "#auth-2fa"];
  show($(panels[n] || panels[0]));
}

function playEnterAnimation(el) {
  if (!el) return;
  el.classList.remove("view-enter");
  void el.offsetWidth;
  el.classList.add("view-enter");
  el.addEventListener(
    "animationend",
    () => el.classList.remove("view-enter"),
    { once: true }
  );
}

function showView(name) {
  hide($("#view-gate"));
  hide($("#view-account"));
  hide($("#view-auth"));
  hide($("#view-app"));
  let target = null;
  if (name === "gate") target = $("#view-gate");
  if (name === "account") target = $("#view-account");
  if (name === "auth") target = $("#view-auth");
  if (name === "app") target = $("#view-app");
  if (target) {
    show(target);
    playEnterAnimation(target);
  }
}

function showAccountView(allowRegister = true) {
  accountRegisterMode = false;
  updateAccountFormMode(allowRegister);
  showView("account");
}

function updateAccountFormMode(allowRegister = true) {
  const title = $("#account-title");
  const sub = $("#account-sub");
  const submit = $("#btn-account-submit");
  const toggle = $("#btn-account-toggle-mode");
  if (accountRegisterMode) {
    if (title) title.textContent = "Daftar akun";
    if (sub) sub.textContent = "Buat akun untuk menyimpan koneksi Telegram di server.";
    if (submit) submit.textContent = "Daftar";
    if (toggle) toggle.textContent = "Sudah punya akun? Masuk";
  } else {
    if (title) title.textContent = "Masuk";
    if (sub) sub.textContent = "Login akun Telegram Drive — koneksi Telegram diatur setelah masuk.";
    if (submit) submit.textContent = "Masuk";
    if (toggle) {
      toggle.textContent = allowRegister ? "Buat akun baru" : "";
      toggle.style.display = allowRegister ? "" : "none";
    }
  }
}

function applyTelegramRoute(tg) {
  if (tg?.authenticated) {
    enterApp(tg);
    return;
  }
  showView("auth");
  if (tg?.step === "code") setAuthStep(2);
  else if (tg?.step === "phone") setAuthStep(1);
  else setAuthStep(0);
}

async function bootstrapAfterGate() {
  try {
    const me = await api("/api/account/me");
    accountUsername = me.username || "";
    accountIsAdmin = !!me.is_admin;
    applyTelegramRoute(me.telegram);
  } catch (e) {
    if (e.message === "gate_required") {
      showView("gate");
      return;
    }
    showAccountView(config.registration_enabled !== false);
  }
}

async function refreshSettingsTelegramStatus() {
  const box = $("#settings-telegram-status");
  const btnSetup = $("#btn-settings-telegram-setup");
  const btnDisc = $("#btn-settings-telegram-disconnect");
  if (!box) return;
  try {
    const st = await api("/api/auth/status");
    if (st.authenticated) {
      const u = st.user || {};
      box.textContent = `Terhubung: ${u.first_name || "User"}${u.username ? " @" + u.username : ""}${u.phone ? " · " + u.phone : ""}`;
      if (btnSetup) btnSetup.textContent = "Ubah koneksi Telegram";
      if (btnDisc) show(btnDisc);
    } else {
      box.textContent = "Belum terhubung — atur API ID, nomor, dan OTP sekali saja.";
      if (btnSetup) btnSetup.textContent = "Hubungkan Telegram";
      if (btnDisc) hide(btnDisc);
    }
  } catch (e) {
    box.textContent = e.message || "Gagal memuat status Telegram.";
    if (btnDisc) hide(btnDisc);
  }
}

function resetChangePasswordForm() {
  hideError($("#change-password-error"));
  const ok = $("#change-password-ok");
  if (ok) hide(ok);
  const form = $("#form-change-password");
  if (form) form.reset();
}

function renderUserBox(u) {
  const name = [u.first_name, u.last_name].filter(Boolean).join(" ").trim() || "User";
  const lines = [];
  if (u.username) lines.push(`<span class="user-line user-handle">@${escapeHtml(u.username)}</span>`);
  if (u.phone) lines.push(`<span class="user-line user-phone">${escapeHtml(u.phone)}</span>`);
  if (accountUsername) {
    lines.push(`<span class="user-line user-account">Akun: ${escapeHtml(accountUsername)}</span>`);
  }
  return `<strong class="user-display-name">${escapeHtml(name)}</strong>${lines.join("")}`;
}

function setAdminYtdlpCheckResult(data) {
  const box = $("#admin-ytdlp-check");
  if (!box) return;
  box.classList.remove("is-ok", "is-bad", "is-pending", "hidden");
  if (!data) {
    hide(box);
    return;
  }
  const lines = [data.message || ""];
  if (data.valid && data.test_video_title) {
    lines.push(`Tes video: ${data.test_video_title}`);
  }
  if (data.google_cookie_count != null && data.youtube_cookie_count != null) {
    lines.push(`Cookie: YouTube ${data.youtube_cookie_count}, Google ${data.google_cookie_count}`);
  } else if (data.youtube_cookie_count != null || data.relevant_cookie_count != null) {
    lines.push(`Cookie relevan: ${data.relevant_cookie_count ?? data.youtube_cookie_count}`);
  }
  if (data.format_count != null && data.valid) {
    lines.push(`Format tersedia: ${data.format_count}`);
  }
  box.textContent = lines.filter(Boolean).join(" · ");
  box.classList.add(data.valid ? "is-ok" : "is-bad");
  show(box);
}

function setAdminYtdlpStatusState(text, state) {
  const status = $("#admin-ytdlp-status");
  if (!status) return;
  status.textContent = text;
  status.classList.remove("is-ok", "is-bad", "is-pending");
  if (state) status.classList.add(state);
}

function buildAdminYtdlpTestFormData() {
  const fd = new FormData();
  const input = $("#admin-ytdlp-file");
  const textEl = $("#admin-ytdlp-text");
  if (ytdlpImportMode === "text") {
    const text = textEl?.value?.trim();
    if (text) fd.append("cookies_text", text);
  } else {
    const file = input?.files?.[0];
    if (file) fd.append("file", file, file.name);
  }
  if (!fd.has("cookies_text") && !fd.has("file")) {
    fd.append("use_saved", "1");
  }
  return fd;
}

async function runAdminYtdlpCookieTest() {
  const errBox = $("#admin-ytdlp-error");
  const okBox = $("#admin-ytdlp-ok");
  const btn = $("#btn-admin-ytdlp-test");
  hideError(errBox);
  if (okBox) hide(okBox);
  setAdminYtdlpCheckResult(null);
  setAdminYtdlpStatusState("Mengecek cookies ke YouTube…", "is-pending");
  setBtnLoading(btn, true, "Mengecek…");
  try {
    const r = await fetch("/api/admin/ytdlp-cookies/test", {
      method: "POST",
      body: buildAdminYtdlpTestFormData(),
      credentials: "same-origin",
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(apiErrorMessage(data, r.statusText));
    setAdminYtdlpCheckResult(data);
    setAdminYtdlpStatusState(
      data.valid ? "Hasil cek: cookies siap dipakai" : "Hasil cek: cookies bermasalah",
      data.valid ? "is-ok" : "is-bad"
    );
  } catch (err) {
    setAdminYtdlpCheckResult({ valid: false, message: err.message });
    setAdminYtdlpStatusState("Cek cookies gagal", "is-bad");
  } finally {
    setBtnLoading(btn, false, "");
  }
}

async function refreshAdminDonationForm() {
  const status = $("#admin-donation-status");
  const errBox = $("#admin-donation-error");
  const okBox = $("#admin-donation-ok");
  hideError(errBox);
  if (okBox) hide(okBox);
  if (status) {
    status.textContent = "Memuat pengaturan QRIS…";
    status.classList.remove("is-ok", "is-bad");
  }
  try {
    const data = await api("/api/admin/donation");
    const enabledEl = $("#admin-donation-enabled");
    const saweriaEl = $("#admin-donation-saweria");
    const qrisEl = $("#admin-donation-qris");
    if (enabledEl) enabledEl.checked = !!data.enabled;
    if (saweriaEl) saweriaEl.value = data.saweria_url || "";
    if (qrisEl) qrisEl.value = data.qris_payload || "";
    const preview = $("#admin-donation-qr-preview");
    if (preview) {
      if (data.qr_available && data.enabled) {
        preview.src = `${DONATION_QR_URL}?t=${Date.now()}`;
        show(preview);
      } else {
        hide(preview);
      }
    }
    if (status) {
      status.textContent = data.configured
        ? `QRIS tersimpan · ${data.payload_length} karakter`
        : "Pakai default server — ubah lalu Simpan QRIS untuk menyimpan custom";
      status.classList.add("is-ok");
    }
    applyDonationInfo({
      enabled: data.enabled,
      saweria_url: data.saweria_url,
      qr_available: data.qr_available,
    });
  } catch (err) {
    if (status) {
      status.textContent = err.message || "Gagal memuat pengaturan QRIS";
      status.classList.add("is-bad");
    }
  }
}

async function refreshAdminSections() {
  const donationSec = $("#settings-admin-donation-section");
  const ytdlpSec = $("#settings-admin-section");
  if (!accountIsAdmin) {
    setAdminSectionVisible(donationSec, false);
    setAdminSectionVisible(ytdlpSec, false);
    return;
  }
  setAdminSectionVisible(donationSec, true);
  await refreshAdminDonationForm();
  setAdminSectionVisible(ytdlpSec, false);
}

function openSettingsPage() {
  renderThemePicker();
  refreshSettingsTelegramStatus();
  refreshAdminSections();
  const nameEl = $("#settings-account-name");
  if (nameEl) nameEl.textContent = accountUsername || "—";
}

function updateYtdlpFileZoneLabel() {
  const input = $("#admin-ytdlp-file");
  const nameEl = $("#admin-ytdlp-filename");
  const zone = $("#ytdlp-file-zone");
  const file = input?.files?.[0];
  if (nameEl) {
    nameEl.textContent = file ? file.name : "Belum ada file dipilih";
  }
  zone?.classList.toggle("has-file", !!file);
}

let ytdlpImportMode = "file";

function showYtdlpImportMode(mode) {
  ytdlpImportMode = mode === "text" ? "text" : "file";
  $("#ytdlp-panel-file")?.classList.toggle("hidden", ytdlpImportMode !== "file");
  $("#ytdlp-panel-text")?.classList.toggle("hidden", ytdlpImportMode !== "text");
  $$(".ytdlp-import-tab").forEach((el) => {
    el.classList.toggle("active", el.dataset.ytdlpMode === ytdlpImportMode);
  });
}

$$(".ytdlp-import-tab").forEach((btn) => {
  btn.addEventListener("click", () => showYtdlpImportMode(btn.dataset.ytdlpMode || "file"));
});

$("#admin-ytdlp-file")?.addEventListener("change", updateYtdlpFileZoneLabel);

const ytdlpFileZone = $("#ytdlp-file-zone");
if (ytdlpFileZone) {
  ["dragenter", "dragover"].forEach((ev) => {
    ytdlpFileZone.addEventListener(ev, (e) => {
      e.preventDefault();
      ytdlpFileZone.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach((ev) => {
    ytdlpFileZone.addEventListener(ev, (e) => {
      e.preventDefault();
      ytdlpFileZone.classList.remove("dragover");
    });
  });
  ytdlpFileZone.addEventListener("drop", (e) => {
    const input = $("#admin-ytdlp-file");
    const files = e.dataTransfer?.files;
    if (input && files?.length) {
      input.files = files;
      updateYtdlpFileZoneLabel();
    }
  });
}

$("#btn-admin-ytdlp-test")?.addEventListener("click", () => runAdminYtdlpCookieTest());

$("#form-admin-ytdlp-cookies")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("#admin-ytdlp-file");
  const textEl = $("#admin-ytdlp-text");
  const errBox = $("#admin-ytdlp-error");
  const okBox = $("#admin-ytdlp-ok");
  hideError(errBox);
  if (okBox) hide(okBox);
  const btn = $("#btn-admin-ytdlp-upload");
  setBtnLoading(btn, true, "Menyimpan…");
  const fd = new FormData();
  if (ytdlpImportMode === "text") {
    const text = textEl?.value?.trim();
    if (!text) {
      setBtnLoading(btn, false, "");
      return showError(errBox, "Tempel JSON atau teks Netscape cookies");
    }
    fd.append("cookies_text", text);
  } else {
    const file = input?.files?.[0];
    if (!file) {
      setBtnLoading(btn, false, "");
      return showError(errBox, "Pilih file cookies (.txt / .json)");
    }
    fd.append("file", file, file.name);
  }
  try {
    const r = await fetch("/api/admin/ytdlp-cookies", {
      method: "POST",
      body: fd,
      credentials: "same-origin",
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(apiErrorMessage(data, r.statusText));
    if (okBox) {
      okBox.textContent = data.message || "Cookies disimpan.";
      okBox.style.color = "var(--ok)";
      show(okBox);
    }
    if (input) input.value = "";
    if (textEl) textEl.value = "";
    updateYtdlpFileZoneLabel();
    await refreshAdminSections();
  } catch (err) {
    showError(errBox, err.message);
  } finally {
    setBtnLoading(btn, false, "");
  }
});

$("#form-admin-donation")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const errBox = $("#admin-donation-error");
  const okBox = $("#admin-donation-ok");
  const btn = $("#btn-admin-donation-save");
  hideError(errBox);
  if (okBox) hide(okBox);
  const qris = $("#admin-donation-qris")?.value?.trim();
  const saweria = $("#admin-donation-saweria")?.value?.trim();
  const enabled = !!$("#admin-donation-enabled")?.checked;
  if (!qris) return showError(errBox, "Payload QRIS wajib diisi");
  if (!saweria) return showError(errBox, "Link Saweria wajib diisi");
  setBtnLoading(btn, true, "Menyimpan…");
  try {
    const data = await api("/api/admin/donation", {
      method: "POST",
      body: { qris_payload: qris, saweria_url: saweria, enabled },
    });
    if (okBox) {
      okBox.textContent = "Pengaturan QRIS disimpan.";
      okBox.style.color = "var(--ok)";
      show(okBox);
    }
    applyDonationInfo({
      enabled: data.enabled,
      saweria_url: data.saweria_url,
      qr_available: data.qr_available,
    });
    const preview = $("#admin-donation-qr-preview");
    if (preview && data.qr_available && data.enabled) {
      preview.src = `${DONATION_QR_URL}?t=${Date.now()}`;
      show(preview);
    }
    const status = $("#admin-donation-status");
    if (status) {
      status.textContent = `QRIS tersimpan · ${data.payload_length} karakter`;
      status.classList.add("is-ok");
    }
  } catch (err) {
    showError(errBox, err.message);
  } finally {
    setBtnLoading(btn, false, "");
  }
});

$("#btn-admin-donation-reset")?.addEventListener("click", async () => {
  const ok = await showConfirm({
    title: "Kembalikan QRIS default?",
    message: "Payload dan link Saweria kembali ke nilai bawaan server.",
    okLabel: "Ya, reset",
    danger: true,
  });
  if (!ok) return;
  const errBox = $("#admin-donation-error");
  const okBox = $("#admin-donation-ok");
  hideError(errBox);
  if (okBox) hide(okBox);
  try {
    const data = await api("/api/admin/donation", { method: "DELETE" });
    if (okBox) {
      okBox.textContent = data.message || "Dikembalikan ke default.";
      okBox.style.color = "var(--ok)";
      show(okBox);
    }
    await refreshAdminDonationForm();
  } catch (err) {
    showError(errBox, err.message);
  }
});

$("#btn-admin-ytdlp-delete")?.addEventListener("click", async () => {
  const ok = await showConfirm({
    title: "Hapus cookies YouTube?",
    message: "Unduhan YouTube akan gagal sampai cookies di-upload lagi.",
    okLabel: "Ya, hapus",
    danger: true,
  });
  if (!ok) return;
  const errBox = $("#admin-ytdlp-error");
  const okBox = $("#admin-ytdlp-ok");
  hideError(errBox);
  if (okBox) hide(okBox);
  try {
    const data = await api("/api/admin/ytdlp-cookies", { method: "DELETE" });
    if (okBox) {
      okBox.textContent = data.message || "Cookies dihapus.";
      okBox.style.color = "var(--ok)";
      show(okBox);
    }
    await refreshAdminSections();
  } catch (err) {
    showError(errBox, err.message);
  }
});

$("#btn-open-change-password")?.addEventListener("click", () => {
  resetChangePasswordForm();
  openModal("modal-change-password");
});

function showAppPanel(panel) {
  currentAppPanel = panel === "settings" ? "settings" : "drive";
  const drivePanel = $("#panel-drive");
  const settingsPanel = $("#panel-settings");
  const foldersNav = $("#sidebar-folders");
  const topbarActions = $("#topbar-actions-drive");
  const fileCount = $("#file-count");

  $$(".sidebar-menu-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.panel === currentAppPanel);
  });

  let activePanel = drivePanel;
  if (currentAppPanel === "settings") {
    hide(drivePanel);
    show(settingsPanel);
    hide(foldersNav);
    hide(topbarActions);
    $("#folder-title").textContent = "Pengaturan";
    if (fileCount) fileCount.textContent = "Tema, akun & Telegram";
    openSettingsPage();
    activePanel = settingsPanel;
  } else {
    show(drivePanel);
    hide(settingsPanel);
    show(foldersNav);
    show(topbarActions);
    setFolderHeader(currentFolderName);
    if (fileCount) updateFileCountLabel();
    activePanel = drivePanel;
  }
  if (activePanel) {
    activePanel.classList.remove("panel-enter");
    void activePanel.offsetWidth;
    activePanel.classList.add("panel-enter");
    activePanel.addEventListener(
      "animationend",
      () => activePanel.classList.remove("panel-enter"),
      { once: true }
    );
  }
  closeMobileSidebar();
}

function isMobileSidebarLayout() {
  return window.matchMedia("(max-width: 900px)").matches;
}

function setMobileSidebarOpen(open) {
  const sb = $("#sidebar");
  const bd = $("#sidebar-backdrop");
  if (!sb) return;
  sb.classList.toggle("mobile-open", open);
  if (!bd) return;
  if (open && isMobileSidebarLayout()) {
    bd.classList.remove("hidden");
    requestAnimationFrame(() => bd.classList.add("is-visible"));
  } else {
    bd.classList.remove("is-visible");
    setTimeout(() => {
      if (!bd.classList.contains("is-visible")) hide(bd);
    }, MOTION_MS);
  }
}

function closeMobileSidebar() {
  setMobileSidebarOpen(false);
}

$$(".sidebar-menu-item").forEach((btn) => {
  btn.addEventListener("click", () => showAppPanel(btn.dataset.panel || "drive"));
});

$("#form-change-password")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  hideError($("#change-password-error"));
  const okBox = $("#change-password-ok");
  if (okBox) hide(okBox);

  const current = $("#pw-current").value;
  const newPw = $("#pw-new").value;
  const newPw2 = $("#pw-new2").value;
  if (newPw !== newPw2) {
    return showError($("#change-password-error"), "Password baru tidak sama");
  }
  if (newPw.length < 6) {
    return showError($("#change-password-error"), "Password baru minimal 6 karakter");
  }

  const btn = $("#btn-change-password");
  setBtnLoading(btn, true, "Menyimpan...");
  try {
    const r = await api("/api/account/change-password", {
      method: "POST",
      body: { current_password: current, new_password: newPw },
    });
    closeModal("modal-change-password");
    $("#form-change-password").reset();
    notifySuccess(r.message || "Password berhasil diubah.");
  } catch (err) {
    showError($("#change-password-error"), err.message);
  } finally {
    setBtnLoading(btn, false, "");
  }
});

$("#btn-settings-telegram-setup")?.addEventListener("click", () => {
  api("/api/auth/status")
    .then((st) => applyTelegramRoute(st))
    .catch(() => {
      showView("auth");
      setAuthStep(0);
    });
});

$("#btn-settings-telegram-disconnect")?.addEventListener("click", async () => {
  const ok = await showConfirm({
    title: "Putuskan Telegram?",
    message: "Session Telegram di server dihapus. Akun aplikasi tetap aktif — hubungkan lagi lewat pengaturan.",
    okLabel: "Ya, putuskan",
    danger: true,
  });
  if (!ok) return;
  try {
    await api("/api/auth/disconnect", { method: "POST" });
    await refreshSettingsTelegramStatus();
    notifySuccess("Koneksi Telegram diputus. Atur ulang lewat Pengaturan.", "Telegram");
  } catch (e) {
    notifyError(e.message);
  }
});

$("#btn-flood-wait-ok")?.addEventListener("click", closeFloodWaitModal);

async function init() {
  initTheme();
  config = await api("/api/config");
  applyDonationInfo(config.donation);
  applyUploadLimitHints();
  await bootstrapAfterGate();
  initDonateQr();
}

function enterApp(st) {
  showView("app");
  showAppPanel("drive");
  const u = st.user || {};
  $("#user-box").innerHTML = renderUserBox(u);
  loadFolders();
}

function setFolderHeader(name) {
  $("#folder-title").textContent = name;
  $("#upload-target-name").textContent = name;
}

function resetFileListUI() {
  const empty = $("#files-empty");
  const grid = $("#files-grid");
  if (grid) grid.innerHTML = "";
  if (empty) {
    empty.textContent = "Memuat daftar file...";
    show(empty);
  }
  if (grid) hide(grid);
}

async function loadFolders(options = {}) {
  const allowMissing = options.allowMissing === true;
  const reloadFiles = options.reloadFiles !== false;
  const { folders } = await api("/api/folders");
  const list = $("#folder-list");
  list.innerHTML = "";
  const activeId = normalizeFolderId(currentFolderId);
  folders.forEach((f) => {
    const item = document.createElement("div");
    item.className = "folder-item" + (sameFolderId(f.id, currentFolderId) ? " active" : "");
    item.dataset.folderId = String(f.id);

    const main = document.createElement("button");
    main.type = "button";
    main.className = "folder-main";
    main.innerHTML = `
      <span class="folder-icon">${f.is_saved ? "★" : "📁"}</span>
      <span class="folder-name">${escapeHtml(f.name)}</span>`;
    main.onclick = () => selectFolder(f.id, f.name, item);
    item.appendChild(main);

    if (!f.is_saved) {
      const del = document.createElement("button");
      del.type = "button";
      del.className = "folder-del";
      del.title = "Hapus folder";
      del.setAttribute("aria-label", "Hapus folder");
      del.textContent = "×";
      del.onclick = (e) => {
        e.stopPropagation();
        deleteFolder(f);
      };
      item.appendChild(del);
    }

    list.appendChild(item);
  });
  let match = folders.find((f) => sameFolderId(f.id, activeId));
  if (!match && allowMissing && activeId !== 0) {
    const item = document.createElement("div");
    item.className = "folder-item active";
    item.dataset.folderId = String(activeId);
    item.innerHTML = `
      <button type="button" class="folder-main">
        <span class="folder-icon">📁</span>
        <span class="folder-name">${escapeHtml(currentFolderName)}</span>
      </button>
      <button type="button" class="folder-del" title="Hapus folder" aria-label="Hapus folder">×</button>`;
    item.querySelector(".folder-main").onclick = () =>
      selectFolder(activeId, currentFolderName, item);
    item.querySelector(".folder-del").onclick = (e) => {
      e.stopPropagation();
      deleteFolder({ id: activeId, name: currentFolderName });
    };
    list.appendChild(item);
    match = { id: activeId, name: currentFolderName };
  }
  if (!match && folders.length) {
    const first = list.querySelector(".folder-item");
    await selectFolder(folders[0].id, folders[0].name, first);
    return;
  }
  if (match) {
    currentFolderName = match.name;
    setFolderHeader(match.name);
    $$(".folder-item").forEach((el) => {
      el.classList.toggle("active", sameFolderId(el.dataset.folderId, match.id));
    });
  }
  if (reloadFiles) {
    filesPage = 1;
    clearSelection();
    await loadFiles(activeId);
  }
}

async function deleteFolder(folder) {
  const ok = await showConfirm({
    title: "Hapus folder?",
    message: `Channel "${folder.name}" dan semua file di dalamnya akan dihapus permanen dari Telegram. Tidak bisa dibatalkan.`,
    okLabel: "Ya, hapus folder",
    danger: true,
  });
  if (!ok) return;
  try {
    await api(`/api/folders/${folder.id}`, { method: "DELETE" });
    if (sameFolderId(currentFolderId, folder.id)) {
      currentFolderId = 0;
      currentFolderName = "Saved Messages";
    }
    await loadFolders();
    notifySuccess(`Folder "${folder.name}" dihapus.`);
  } catch (e) {
    if (!isFloodWaitError(e)) notifyError(e.message);
  }
}

function initDonateQr() {
  const img = $("#notify-donate-qr");
  const fallback = $("#notify-donate-qr-fallback");
  if (!img || donateQrBound) return;
  donateQrBound = true;

  const onOk = () => {
    show(img);
    if (fallback) hide(fallback);
  };
  const onFail = () => {
    hide(img);
    if (fallback) show(fallback);
  };

  img.addEventListener("load", onOk);
  img.addEventListener("error", onFail);

  if (img.complete) {
    if (img.naturalWidth > 0) onOk();
    else onFail();
  }
}

function ensureDonateQrVisible() {
  const img = $("#notify-donate-qr");
  const fallback = $("#notify-donate-qr-fallback");
  if (!img || !donationInfo.qr_available) return;
  if (img.naturalWidth > 0) {
    show(img);
    if (fallback) hide(fallback);
    return;
  }
  img.src = `${DONATION_QR_URL}?t=${Date.now()}`;
}

function showNotifyModal(title, message, options = {}) {
  const type = options.type || "info";

  const card = $("#modal-notify-card");
  if (card) {
    card.classList.remove("notify-success", "notify-error", "notify-info");
    card.classList.add(`notify-${type}`);
  }
  const titleEl = $("#notify-title");
  const msgEl = $("#notify-message");
  if (titleEl) titleEl.textContent = title;
  if (msgEl) msgEl.textContent = message;

  const okBtn = $("#notify-ok-btn");
  if (okBtn) {
    okBtn.classList.toggle("danger", type === "error");
    okBtn.textContent = "Siap tuan!";
  }

  updateNotifyDonateBlock();
  ensureDonateQrVisible();
  openModal("modal-notify");
}

$("#notify-ok-btn")?.addEventListener("click", () => closeModal("modal-notify"));

function notifySuccess(message, title = "Berhasil") {
  showNotifyModal(title, message, { type: "success" });
}

function notifyError(message, title = "Gagal") {
  showNotifyModal(title, message, { type: "error" });
}

function showAlertModal(title, message) {
  const type = title === "Gagal" ? "error" : "info";
  showNotifyModal(title, message, { type });
}

async function selectFolder(id, name, itemEl) {
  if (currentAppPanel === "settings") showAppPanel("drive");
  const folderId = normalizeFolderId(id);
  currentFolderId = folderId;
  currentFolderName = name;
  setFolderHeader(name);
  $$(".folder-item").forEach((b) => {
    b.classList.toggle("active", itemEl ? b === itemEl : sameFolderId(b.dataset.folderId, folderId));
  });
  if (itemEl) itemEl.classList.add("active");
  filesPage = 1;
  clearSelection();
  await loadFiles(folderId);
  closeMobileSidebar();
}

function scheduleFilesSearch() {
  if (filesSearchTimer) clearTimeout(filesSearchTimer);
  filesSearchTimer = setTimeout(() => {
    filesSearchTimer = null;
    const el = $("#files-search");
    const next = (el?.value || "").trim();
    if (next === filesSearch) return;
    filesSearch = next;
    filesPage = 1;
    clearSelection();
    loadFiles(currentFolderId);
  }, 350);
}

$$(".file-filter").forEach((btn) => {
  btn.addEventListener("click", () => {
    const ft = btn.dataset.fileFilter || "all";
    if (ft === filesFilter) return;
    filesFilter = ft;
    filesPage = 1;
    clearSelection();
    syncFileFilterButtons();
    loadFiles(currentFolderId);
  });
});

$("#files-search")?.addEventListener("input", scheduleFilesSearch);
$("#files-search")?.addEventListener("search", () => {
  const el = $("#files-search");
  filesSearch = (el?.value || "").trim();
  filesPage = 1;
  clearSelection();
  loadFiles(currentFolderId);
});

function clearSelection() {
  selectedIds.clear();
  updateBulkBar();
  const all = $("#select-all-files");
  if (all) all.checked = false;
}

function updateBulkBar() {
  const n = selectedIds.size;
  const bar = $("#bulk-bar");
  if (!bar) return;
  if (n > 0) {
    show(bar);
    $("#bulk-count").textContent = `${n} file dipilih`;
  } else {
    hide(bar);
  }
}

function toggleFileSelection(id, checked) {
  if (checked) selectedIds.add(id);
  else selectedIds.delete(id);
  updateBulkBar();
  syncSelectAllCheckbox();
}

function syncSelectAllCheckbox() {
  const all = $("#select-all-files");
  if (!all || !loadedFiles.length) return;
  all.checked = selectedIds.size === loadedFiles.length;
  all.indeterminate = selectedIds.size > 0 && selectedIds.size < loadedFiles.length;
}

const FILE_FILTER_LABELS = {
  all: "file",
  photo: "foto",
  video: "video",
  document: "dokumen",
};

function updateFileCountLabel() {
  const el = $("#file-count");
  if (!el) return;
  const total = filesListMeta.total || 0;
  const page = filesListMeta.page || 1;
  const pages = filesListMeta.total_pages || 1;
  const ft = filesListMeta.filter || "all";
  const q = (filesListMeta.q || "").trim();
  let label = FILE_FILTER_LABELS[ft] || "file";
  if (total === 0) {
    el.textContent = q || ft !== "all" ? `0 ${label}` : "0 file";
    return;
  }
  if (pages > 1) {
    el.textContent = `${total} ${label} · hal ${page}/${pages}`;
  } else {
    el.textContent = `${total} ${label}`;
  }
}

function renderFilesPagination() {
  const top = $("#files-pagination-top");
  const bottom = $("#files-pagination-bottom");
  const pages = filesListMeta.total_pages || 1;
  const page = filesListMeta.page || 1;
  const total = filesListMeta.total || 0;

  if (!total || pages <= 1) {
    hide(top);
    hide(bottom);
    if (top) top.innerHTML = "";
    if (bottom) bottom.innerHTML = "";
    return;
  }

  const html = buildPaginationHtml(page, pages);
  [top, bottom].forEach((el) => {
    if (!el) return;
    el.innerHTML = html;
    show(el);
    el.querySelectorAll("[data-page]").forEach((btn) => {
      btn.onclick = () => {
        const p = parseInt(btn.dataset.page, 10);
        if (!Number.isFinite(p) || p === filesPage) return;
        filesPage = p;
        clearSelection();
        loadFiles(currentFolderId);
      };
    });
  });
}

function buildPaginationHtml(page, pages) {
  const prev = page > 1 ? page - 1 : null;
  const next = page < pages ? page + 1 : null;
  const nums = [];
  const window = 2;
  for (let p = 1; p <= pages; p += 1) {
    if (p === 1 || p === pages || (p >= page - window && p <= page + window)) {
      nums.push(p);
    } else if (nums[nums.length - 1] !== "…") {
      nums.push("…");
    }
  }
  const parts = [];
  parts.push(
    `<button type="button" class="btn ghost sm page-btn" data-page="${prev || ""}" ${prev ? "" : "disabled"}>← Sebelumnya</button>`
  );
  nums.forEach((n) => {
    if (n === "…") {
      parts.push('<span class="page-ellipsis">…</span>');
      return;
    }
    const active = n === page ? " active" : "";
    parts.push(
      `<button type="button" class="btn ghost sm page-num${active}" data-page="${n}">${n}</button>`
    );
  });
  parts.push(
    `<button type="button" class="btn ghost sm page-btn" data-page="${next || ""}" ${next ? "" : "disabled"}>Berikutnya →</button>`
  );
  return `<div class="files-pagination-inner">${parts.join("")}</div>`;
}

function syncFileFilterButtons() {
  $$(".file-filter").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.fileFilter === filesFilter);
  });
}

function renderFileList(files, folderId, meta = {}) {
  const empty = $("#files-empty");
  const grid = $("#files-grid");
  loadedFiles = files;
  filesListMeta = {
    total: meta.total ?? files.length,
    total_pages: meta.total_pages ?? 1,
    page: meta.page ?? filesPage,
    filter: meta.filter ?? filesFilter,
    q: meta.q ?? filesSearch,
    scan_limit_reached: !!meta.scan_limit_reached,
  };
  updateFileCountLabel();
  renderFilesPagination();
  syncSelectAllCheckbox();
  if (!files.length) {
    clearSelection();
    show(empty);
    hide(grid);
    const ft = filesListMeta.filter || "all";
    const q = (filesListMeta.q || "").trim();
    if (q) {
      empty.textContent = `Tidak ada file yang cocok dengan “${q}”.`;
    } else if (ft !== "all") {
      const label = FILE_FILTER_LABELS[ft] || ft;
      empty.textContent = `Tidak ada ${label} di folder ini.`;
    } else {
      empty.textContent =
        folderId === 0
          ? "Belum ada file media di Saved Messages. Upload file di atas."
          : "Belum ada file di folder ini. Upload file di atas.";
    }
    if (filesListMeta.scan_limit_reached && filesListMeta.total > 0) {
      empty.textContent += " (daftar dibatasi pemindaian server)";
    }
    return;
  }
  hide(empty);
  show(grid);
  grid.innerHTML = "";
  files.forEach((f) => {
    const checked = selectedIds.has(f.id);
    const card = document.createElement("article");
    card.className = "file-card" + (checked ? " selected" : "");
    card.innerHTML = `
        <label class="file-check">
          <input type="checkbox" class="file-select" data-id="${f.id}" ${checked ? "checked" : ""}>
        </label>
        ${renderFileThumb(folderId, f)}
        <div class="file-card-body">
          ${renderFileCardName(f)}
          <div class="file-card-meta">
            <span>${escapeHtml(f.sizeStr || formatSize(f.size))}</span>
            <span>${formatDate(f.date)}</span>
          </div>
        </div>
        <div class="file-card-actions">
          <a class="btn ghost sm" href="/api/download/${folderId}/${f.id}" download>Download</a>
          <button type="button" class="btn danger sm del" data-id="${f.id}">Hapus</button>
        </div>`;
    const delBtn = card.querySelector(".del");
    delBtn.dataset.name = f.name;
    const cb = card.querySelector(".file-select");
    cb.onchange = () => {
      toggleFileSelection(f.id, cb.checked);
      card.classList.toggle("selected", cb.checked);
    };
    grid.appendChild(card);
  });
  updateBulkBar();
  const openPreviewHandler = (el) => {
    el.onclick = (e) => {
      e.preventDefault();
      const id = parseInt(el.dataset.previewId, 10);
      const file = files.find((x) => x.id === id);
      if (file) openFilePreview(folderId, file);
    };
  };
  grid.querySelectorAll("[data-preview-id]").forEach(openPreviewHandler);
  grid.querySelectorAll(".del").forEach((btn) => {
    btn.onclick = async () => {
      const name = btn.dataset.name || "file ini";
      const ok = await showConfirm({
        title: "Hapus file?",
        message: `File "${name}" akan dihapus permanen dari Telegram.`,
        okLabel: "Ya, hapus",
        danger: true,
      });
      if (!ok) return;
      try {
        await api(`/api/files/${folderId}/${btn.dataset.id}`, { method: "DELETE" });
        await loadFiles(folderId);
        notifySuccess(`"${name}" dihapus dari Telegram.`);
      } catch (err) {
        if (!isFloodWaitError(err)) notifyError(err.message);
      }
    };
  });
}

function buildFilesQuery(folderId) {
  const params = new URLSearchParams({
    folder_id: String(folderId),
    filter: filesFilter,
    page: String(filesPage),
    per_page: String(FILES_PER_PAGE),
    _: String(Date.now()),
  });
  const q = filesSearch.trim();
  if (q) params.set("q", q);
  return params.toString();
}

async function loadFiles(targetFolderId = currentFolderId) {
  const folderId = normalizeFolderId(targetFolderId);
  const reqId = ++filesLoadGen;

  if (filesFetchAbort) filesFetchAbort.abort();
  filesFetchAbort = new AbortController();

  resetFileListUI();
  syncFileFilterButtons();
  const searchEl = $("#files-search");
  if (searchEl && searchEl.value !== filesSearch) searchEl.value = filesSearch;

  try {
    const res = await fetch(`/api/files?${buildFilesQuery(folderId)}`, {
      credentials: "same-origin",
      cache: "no-store",
      signal: filesFetchAbort.signal,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const msg = handleApiError(data, res.statusText);
      const err = new Error(msg);
      if (parseApiDetail(data)) err.code = "flood_wait";
      throw err;
    }

    if (reqId !== filesLoadGen || !sameFolderId(folderId, currentFolderId)) return;

    filesPage = data.page || filesPage;
    renderFileList(data.files || [], folderId, data);
  } catch (e) {
    if (e.name === "AbortError") return;
    if (reqId !== filesLoadGen || !sameFolderId(folderId, currentFolderId)) return;
    const empty = $("#files-empty");
    show(empty);
    hide($("#files-grid"));
    empty.textContent = e.message;
  }
}

$("#gate-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  hideError($("#gate-error"));
  try {
    await api("/api/gate/login", { method: "POST", body: { password: $("#gate-password").value } });
    await bootstrapAfterGate();
  } catch (err) {
    showError($("#gate-error"), err.message);
  }
});

$("#account-login-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  hideError($("#account-error"));
  const username = $("#account-username").value.trim().toLowerCase();
  const password = $("#account-password").value;
  if (!/^[a-z0-9_]{3,32}$/.test(username)) {
    return showError($("#account-error"), "Username: 3–32 karakter, huruf kecil, angka, underscore (_) saja");
  }
  const path = accountRegisterMode ? "/api/account/register" : "/api/account/login";
  const btn = $("#btn-account-submit");
  setBtnLoading(btn, true, accountRegisterMode ? "Mendaftar..." : "Masuk...");
  try {
    const r = await api(path, { method: "POST", body: { username, password } });
    accountUsername = r.username || username;
    accountIsAdmin = !!r.is_admin;
    applyTelegramRoute(r.telegram);
  } catch (err) {
    showError($("#account-error"), err.message);
  } finally {
    setBtnLoading(btn, false, "");
  }
});

$("#btn-account-toggle-mode")?.addEventListener("click", () => {
  accountRegisterMode = !accountRegisterMode;
  updateAccountFormMode(config.registration_enabled !== false);
  hideError($("#account-error"));
});

$("#btn-setup").onclick = async () => {
  hideError($("#auth-error"));
  const api_id = parseInt($("#api-id").value, 10);
  const api_hash = $("#api-hash").value.trim();
  if (!api_id || !api_hash) return showError($("#auth-error"), "API ID dan Hash wajib diisi");
  setBtnLoading($("#btn-setup"), true, "Menghubungkan...");
  try {
    const st = await api("/api/auth/configure", { method: "POST", body: { api_id, api_hash } });
    if (st.authenticated) return enterApp(st);
    if (st.step === "code") setAuthStep(2);
    else if (st.step === "phone") setAuthStep(1);
    else setAuthStep(1);
  } catch (e) {
    showError($("#auth-error"), e.message);
  } finally {
    setBtnLoading($("#btn-setup"), false, "");
  }
};

$("#btn-back-setup").onclick = () => setAuthStep(0);

$("#btn-phone").onclick = async () => {
  hideError($("#auth-error"));
  const phone = $("#phone").value.trim();
  if (!phone) return showError($("#auth-error"), "Isi nomor telepon (+62...)");
  setBtnLoading($("#btn-phone"), true, "Mengirim OTP...");
  try {
    await api("/api/auth/phone", { method: "POST", body: { phone } });
    setAuthStep(2);
    const box = $("#auth-error");
    box.textContent = "Kode dikirim — buka aplikasi Telegram (chat «Telegram» / SMS).";
    box.style.color = "var(--ok)";
    show(box);
  } catch (e) {
    $("#auth-error").style.color = "";
    showError($("#auth-error"), e.message);
  } finally {
    setBtnLoading($("#btn-phone"), false, "");
  }
};

$("#btn-code").onclick = async () => {
  hideError($("#auth-error"));
  $("#auth-error").style.color = "";
  setBtnLoading($("#btn-code"), true, "Memverifikasi...");
  try {
    const r = await api("/api/auth/code", { method: "POST", body: { code: $("#code").value.trim() } });
    if (r.status === "password_required") {
      setAuthStep(3);
      return;
    }
    if (r.status === "ok") enterApp({ authenticated: true, user: r.user });
  } catch (e) {
    showError($("#auth-error"), e.message);
  } finally {
    setBtnLoading($("#btn-code"), false, "");
  }
};

$("#btn-2fa").onclick = async () => {
  hideError($("#auth-error"));
  setBtnLoading($("#btn-2fa"), true, "Masuk...");
  try {
    const r = await api("/api/auth/password", { method: "POST", body: { password: $("#twofa").value } });
    if (r.status === "ok") enterApp({ authenticated: true, user: r.user });
  } catch (e) {
    showError($("#auth-error"), e.message);
  } finally {
    setBtnLoading($("#btn-2fa"), false, "");
  }
};

$("#btn-logout").onclick = async () => {
  const ok = await showConfirm({
    title: "Keluar dari akun?",
    message: "Anda logout dari akun aplikasi. Koneksi Telegram tetap tersimpan di server untuk akun ini.",
    okLabel: "Ya, keluar",
    danger: true,
  });
  if (!ok) return;
  try {
    await api("/api/account/logout", { method: "POST" });
  } catch {
    /* cookie mungkin sudah habis */
  }
  accountUsername = "";
  accountIsAdmin = false;
  showAccountView(config.registration_enabled !== false);
};

$("#btn-menu")?.addEventListener("click", () => {
  const open = !$("#sidebar")?.classList.contains("mobile-open");
  setMobileSidebarOpen(open);
});

$("#btn-sidebar-close")?.addEventListener("click", () => {
  if (isMobileSidebarLayout()) closeMobileSidebar();
});

$("#sidebar-backdrop")?.addEventListener("click", closeMobileSidebar);

$("#btn-new-folder")?.addEventListener("click", () => {
  $("#new-folder-name").value = "";
  hideError($("#folder-create-error"));
  openModal("modal-folder");
  $("#new-folder-name").focus();
});

$("#btn-create-folder")?.addEventListener("click", async () => {
  const name = $("#new-folder-name").value.trim();
  hideError($("#folder-create-error"));
  if (!name) return showError($("#folder-create-error"), "Isi nama folder");
  const ok = await showConfirm({
    title: "Buat folder baru?",
    message: `Channel privat "${name} [TD]" akan dibuat di akun Telegram Anda.`,
    okLabel: "Ya, buat folder",
  });
  if (!ok) return;
  closeAllModals();
  setBtnLoading($("#btn-create-folder"), true, "Membuat...");
  try {
    const { folder } = await api("/api/folders", { method: "POST", body: { name } });
    currentFolderId = normalizeFolderId(folder.id);
    currentFolderName = folder.name;
    setFolderHeader(folder.name);
    await loadFolders({ allowMissing: true, reloadFiles: false });
    await loadFiles(currentFolderId);
    notifySuccess(`Folder "${folder.name}" dibuat.`);
  } catch (e) {
    if (!isFloodWaitError(e)) notifyError(e.message);
    openModal("modal-folder");
    showError($("#folder-create-error"), e.message);
  } finally {
    setBtnLoading($("#btn-create-folder"), false, "");
  }
});

const fileInput = $("#file-input");
const dropzone = $("#dropzone");

function onFilesSelected(fileList) {
  const files = fileList ? [...fileList] : [];
  if (!files.length) {
    clearUploadPreview();
    $("#upload-label").textContent = "Belum ada file dipilih (bisa banyak sekaligus)";
    validatePendingUploadFiles([]);
    return;
  }
  if (files.length === 1) {
    $("#upload-label").textContent = files[0].name;
  } else {
    $("#upload-label").textContent = `${files.length} file dipilih`;
  }
  renderLocalUploadPreview(files);
}

["dragenter", "dragover"].forEach((ev) => {
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  });
});
["dragleave", "drop"].forEach((ev) => {
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
  });
});
fileInput.onchange = () => onFilesSelected(fileInput.files);

dropzone.addEventListener("drop", (e) => {
  if (e.dataTransfer.files.length) {
    fileInput.files = e.dataTransfer.files;
    onFilesSelected(e.dataTransfer.files);
  }
});

async function doBulkUpload(files) {
  if (!validatePendingUploadFiles(files)) return;

  const st = $("#upload-status");
  hide(st);
  const fd = new FormData();
  fd.append("folder_id", String(currentFolderId));
  for (const file of files) {
    fd.append("files", file, file.name);
  }
  const totalSize = files.reduce((s, f) => s + f.size, 0);
  const btn = $("#btn-upload");
  btn.dataset.loading = "1";
  setBtnLoading(btn, true, "Mengupload...");
  showTransferLoader(
    files.length > 1 ? `Upload ${files.length} file` : "Upload file",
    `Total ${formatSize(totalSize)}`
  );
  try {
    const data = await xhrPostForm("/api/upload/bulk", fd, (loaded, total) => {
      const pct = total > 0 ? Math.round((loaded / total) * 100) : null;
      const detail =
        total > 0
          ? `Mengupload… ${formatSize(loaded)} / ${formatSize(total)} (${Math.round((loaded / total) * 100)}%)`
          : `Mengupload… ${formatSize(loaded)}`;
      updateTransferProgress(pct, detail, "upload");
    });
    const okN = data.uploaded?.length || 0;
    const errN = data.errors?.length || 0;
    updateTransferProgress(100, "Selesai.", "upload");
    if (errN > 0 && okN > 0) {
      showNotifyModal(
        "Selesai sebagian",
        `${okN} file berhasil, ${errN} file gagal diunggah.`,
        { type: "info" }
      );
    } else if (errN > 0) {
      notifyError(`${errN} file gagal diunggah.`);
    } else {
      notifySuccess(
        okN === 1 ? "1 file berhasil diunggah." : `${okN} file berhasil diunggah.`
      );
    }
    fileInput.value = "";
    clearUploadPreview();
    $("#upload-label").textContent = "Belum ada file dipilih (bisa banyak sekaligus)";
    validatePendingUploadFiles([]);
    await loadFiles();
  } catch (e) {
    if (!isFloodWaitError(e)) notifyError(e.message);
  } finally {
    delete btn.dataset.loading;
    setBtnLoading(btn, false, "");
    hideTransferLoader();
    hide(st);
    validatePendingUploadFiles(pendingUploadFiles.length ? pendingUploadFiles : []);
  }
}

$("#btn-upload").onclick = async () => {
  const files =
    pendingUploadFiles.length > 0
      ? pendingUploadFiles
      : fileInput.files
        ? [...fileInput.files]
        : [];
  if (!files.length) {
    notifyError("Pilih file dulu.");
    return;
  }
  if (!validatePendingUploadFiles(files)) return;
  const totalSize = files.reduce((s, f) => s + f.size, 0);
  const ok = await showConfirm({
    title: files.length > 1 ? "Upload banyak file?" : "Upload file?",
    message: `Upload ${files.length} file (total ${formatSize(totalSize)}) ke folder "${currentFolderName}".`,
    extraHtml: $("#upload-preview").innerHTML || undefined,
    okLabel: "Ya, upload",
  });
  if (!ok) return;
  await doBulkUpload(files);
};

function showIngestTab(mode) {
  const isUrl = mode === "url";
  $("#ingest-panel-file")?.classList.toggle("hidden", isUrl);
  $("#ingest-panel-url")?.classList.toggle("hidden", !isUrl);
  $$(".ingest-tab").forEach((el) => {
    el.classList.toggle("active", el.dataset.ingest === mode);
  });
  if (isUrl) scheduleImportUrlProbe();
}

$$(".ingest-tab").forEach((btn) => {
  btn.addEventListener("click", () => showIngestTab(btn.dataset.ingest || "file"));
});

let importFilenameTouched = false;
let importUrlProbeTimer = null;
let importUrlProbeGen = 0;

function resetImportFilenameAutofill() {
  importFilenameTouched = false;
  importUrlProbeGen += 1;
  const hint = $("#import-filename-hint");
  if (hint) hide(hint);
}

function setImportFilenameHint(text, isError = false) {
  const hint = $("#import-filename-hint");
  if (!hint) return;
  if (!text) {
    hide(hint);
    return;
  }
  hint.textContent = text;
  hint.style.color = isError ? "var(--danger)" : "";
  show(hint);
}

function scheduleImportUrlProbe() {
  clearTimeout(importUrlProbeTimer);
  importUrlProbeTimer = setTimeout(runImportUrlProbe, 450);
}

async function runImportUrlProbe() {
  const url = $("#import-url")?.value?.trim();
  const nameEl = $("#import-filename");
  if (!nameEl) return;

  if (!url || url.length < 12) {
    if (!importFilenameTouched) nameEl.value = "";
    setImportFilenameHint("");
    return;
  }

  const gen = ++importUrlProbeGen;
  if (!importFilenameTouched) {
    nameEl.placeholder = "Mendeteksi nama file…";
  }
  setImportFilenameHint("Memeriksa link…");

  try {
    const data = await api("/api/import/url/probe", { method: "POST", body: { url } });
    if (gen !== importUrlProbeGen) return;
    if (!importFilenameTouched && data.filename) {
      nameEl.value = data.filename;
    }
    if (data.size) {
      const extra = data.duration
        ? ` · ${Math.floor(data.duration / 60)}:${String(data.duration % 60).padStart(2, "0")}`
        : "";
      setImportFilenameHint(
        `Terdeteksi: ${data.filename || "file"} · ${formatSize(data.size)}${extra}`
      );
    } else if (data.filename) {
      setImportFilenameHint("Nama file terdeteksi dari link");
    } else {
      setImportFilenameHint("");
    }
  } catch (e) {
    if (gen !== importUrlProbeGen) return;
    if (!importFilenameTouched) nameEl.value = "";
    setImportFilenameHint(e.message || "Tidak bisa mendeteksi nama file", true);
  } finally {
    if (gen === importUrlProbeGen) {
      nameEl.placeholder = "Tempel URL — nama file terisi otomatis";
    }
  }
}

$("#import-filename")?.addEventListener("input", () => {
  importFilenameTouched = true;
  setImportFilenameHint("");
});

$("#import-url")?.addEventListener("input", scheduleImportUrlProbe);
$("#import-url")?.addEventListener("paste", () => {
  importFilenameTouched = false;
  setTimeout(scheduleImportUrlProbe, 30);
});

$("#btn-import-url")?.addEventListener("click", async () => {
  const url = $("#import-url")?.value?.trim();
  const filename = $("#import-filename")?.value?.trim();
  const errBox = $("#import-url-error");
  const stBox = $("#import-url-status");
  hideError(errBox);
  if (stBox) hide(stBox);

  if (!url) {
    return showError(errBox, "Masukkan URL unduhan");
  }

  const ok = await showConfirm({
    title: "Import dari link?",
    message: `Server akan mengunduh file lalu menyimpannya ke folder "${currentFolderName}". Proses bisa memakan waktu untuk file besar.`,
    okLabel: "Ya, unduh & simpan",
  });
  if (!ok) return;

  const btn = $("#btn-import-url");
  setBtnLoading(btn, true, "Mengunduh...");
  try {
    const body = { url, folder_id: currentFolderId };
    if (filename) body.filename = filename;
    const r = await importUrlWithProgress(body);
    hide(stBox);
    notifySuccess(
      `${r.file?.name || "File"} (${formatSize(r.bytes || 0)}) disimpan ke folder "${currentFolderName}".`
    );
    $("#import-url").value = "";
    $("#import-filename").value = "";
    resetImportFilenameAutofill();
    await loadFiles();
  } catch (e) {
    if (!isFloodWaitError(e)) {
      hideError(errBox);
      notifyError(e.message);
    }
  } finally {
    setBtnLoading(btn, false, "");
    hideTransferLoader();
  }
});

$("#select-all-files")?.addEventListener("change", (e) => {
  const checked = e.target.checked;
  loadedFiles.forEach((f) => {
    if (checked) selectedIds.add(f.id);
    else selectedIds.delete(f.id);
  });
  $$(".file-select").forEach((cb) => {
    cb.checked = checked;
    cb.closest(".file-card")?.classList.toggle("selected", checked);
  });
  updateBulkBar();
});

$("#btn-bulk-clear")?.addEventListener("click", clearSelection);

$("#btn-bulk-delete")?.addEventListener("click", async () => {
  const ids = [...selectedIds];
  if (!ids.length) return;
  const ok = await showConfirm({
    title: "Hapus file terpilih?",
    message: `${ids.length} file akan dihapus permanen dari Telegram.`,
    okLabel: "Ya, hapus semua",
    danger: true,
  });
  if (!ok) return;
  setBtnLoading($("#btn-bulk-delete"), true, "Menghapus...");
  try {
    await api("/api/files/bulk-delete", {
      method: "POST",
      body: { folder_id: currentFolderId, message_ids: ids },
    });
    clearSelection();
    await loadFiles();
    notifySuccess(
      ids.length === 1 ? "1 file dihapus." : `${ids.length} file dihapus.`
    );
  } catch (e) {
    if (!isFloodWaitError(e)) notifyError(e.message);
  } finally {
    setBtnLoading($("#btn-bulk-delete"), false, "");
  }
});

$("#btn-bulk-download")?.addEventListener("click", async () => {
  const ids = [...selectedIds];
  if (!ids.length) return;
  const ok = await showConfirm({
    title: "Download ZIP?",
    message: `${ids.length} file akan diunduh sebagai satu arsip ZIP.`,
    okLabel: "Ya, download",
  });
  if (!ok) return;
  setBtnLoading($("#btn-bulk-download"), true, "Menyiapkan ZIP...");
  try {
    const r = await fetch("/api/files/bulk-download", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder_id: currentFolderId, message_ids: ids }),
    });
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      throwApiError(data, r.statusText);
    }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `telegram-drive-${currentFolderId}-${ids.length}files.zip`;
    a.click();
    URL.revokeObjectURL(url);
    notifySuccess(
      ids.length === 1
        ? "Download ZIP dimulai (1 file)."
        : `Download ZIP dimulai (${ids.length} file).`
    );
  } catch (e) {
    if (!isFloodWaitError(e)) notifyError(e.message);
  } finally {
    setBtnLoading($("#btn-bulk-download"), false, "");
  }
});

$("#btn-refresh").onclick = () => {
  loadFolders();
  loadFiles();
};

init().catch((e) => {
  console.error(e);
  showView("auth");
});