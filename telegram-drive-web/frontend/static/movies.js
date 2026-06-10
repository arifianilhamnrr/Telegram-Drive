/* LK21 Movie — scrape + HLS proxy backend */

const MoviesPanel = (() => {
  const $ = (sel) => document.querySelector(sel);
  const MOVIES_PER_PAGE = 24;

  let kind = "new";
  let page = 1;
  let moviesListMeta = { page: 1, total_pages: 1, total: 0, per_page: MOVIES_PER_PAGE };
  let searchQuery = "";
  let lastList = null;
  let hlsInstance = null;
  let currentMovie = null;
  let currentMovieUrl = null;
  /** @type {{ m3u8?: string, referer?: string, iframe_url?: string, title?: string } | null} */
  let currentStream = null;
  let movieSaveFolderId = null;
  let movieSaveFolderName = "";
  let embedBridge = null;
  let embedProgressTimer = null;
  let moviesListGen = 0;
  let moviesListAbort = null;
  let movieDetailGen = 0;
  let currentMovieSource = "lk21";
  let currentEpisodeIndex = 0;
  let episodeDownloads = [];
  let currentDownload = null;
  let codeCatalogStatus = { enabled: false, search_available: false };
  let tsPlayerTimer = null;
  let tsPlayerAbort = false;
  let tsPlayerGen = 0;
  let recentPlaybackMarked = null;

  const RECENT_KEY = "td-movie-recent";
  const JAV_CODE_RE = /^[A-Za-z]{2,8}-?\d{2,5}[A-Za-z]?$/i;
  const MAX_RECENT = 24;
  const EMBED_PROGRESS_POLL_MS = 8000;

  function normalizeMovieUrl(url) {
    const s = (url || "").trim();
    if (!s || !/^https?:\/\//i.test(s)) return s;
    try {
      const u = new URL(s);
      if (u.pathname && u.pathname !== "/") {
        u.pathname = u.pathname.replace(/\/+$/, "") + "/";
      }
      return u.href;
    } catch {
      return s;
    }
  }

  function getState() {
    return { kind, page, q: searchQuery, movieUrl: currentMovieUrl };
  }

  function setState(s = {}) {
    if (s.kind) kind = s.kind;
    if (typeof s.page === "number" && s.page > 0) page = s.page;
    if (s.q !== undefined) searchQuery = s.q || "";
    if ("movieUrl" in s) {
      currentMovieUrl = s.movieUrl ? normalizeMovieUrl(s.movieUrl) : null;
    }
  }

  function movieUrlFromLocation() {
    const raw = new URLSearchParams(window.location.search).get("movie");
    if (!raw) return null;
    if (typeof window.tdDecodeMovieDeepLink === "function") {
      return window.tdDecodeMovieDeepLink(raw);
    }
    return /^https?:\/\//i.test(raw) ? normalizeMovieUrl(raw) : null;
  }

  function getProgressKey(url) {
    const u = normalizeMovieUrl(url);
    if (!u) return null;
    return `td-movie-progress:${u}`;
  }

  function loadRecent() {
    try {
      const raw = localStorage.getItem(RECENT_KEY);
      const list = raw ? JSON.parse(raw) : [];
      return Array.isArray(list) ? list : [];
    } catch {
      return [];
    }
  }

  function persistRecent(list) {
    try {
      localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, MAX_RECENT)));
    } catch (e) {}
  }

  function recordRecentWatch(entry) {
    const url = normalizeMovieUrl(entry?.url || "");
    if (!url) return;
    let list = loadRecent().filter((x) => x.url !== url);
    list.unshift({
      url,
      title: (entry.title || "Film").trim() || "Film",
      poster: entry.poster || "",
      progress: Number(entry.progress) || loadProgress(url) || 0,
      duration: Number(entry.duration) || 0,
      watchedAt: Date.now(),
    });
    persistRecent(list);
    renderRecentMovies();
  }

  function markRecentOnPlayback() {
    const url = normalizeMovieUrl(currentMovieUrl);
    if (!url || recentPlaybackMarked === url) return;
    recentPlaybackMarked = url;
    recordRecentWatch({
      url,
      title: currentMovie?.title || "Film",
      poster: currentMovie?.poster || "",
      progress: loadProgress(url),
    });
  }

  function touchRecentProgress(url, seconds, extra = {}) {
    const u = normalizeMovieUrl(url);
    if (!u || !seconds || seconds < 10) return;
    let list = loadRecent();
    const idx = list.findIndex((x) => x.url === u);
    if (idx < 0) {
      return;
    }
    list[idx].progress = seconds;
    if (extra.duration > 0) list[idx].duration = extra.duration;
    list[idx].watchedAt = Date.now();
    if (extra.title) list[idx].title = extra.title;
    if (extra.poster) list[idx].poster = extra.poster;
    persistRecent(list);
    renderRecentMovies();
  }

  function clearRecentHistory() {
    try {
      localStorage.removeItem(RECENT_KEY);
    } catch (e) {}
    renderRecentMovies();
  }

  function renderRecentMovies() {
    const section = $("#movies-recent");
    const grid = $("#movies-recent-grid");
    if (!section || !grid) return;
    const items = loadRecent();
    if (!items.length) {
      hide(section);
      grid.innerHTML = "";
      return;
    }
    show(section);
    grid.innerHTML = items
      .map((item) => {
        const posterInner = item.poster
          ? `<img class="movies-recent-poster" data-poster="${escapeHtml(item.poster)}" src="${escapeHtml(item.poster)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
          : `<div class="movies-recent-poster movies-recent-poster--empty" aria-hidden="true"></div>`;
        const prog = Number(item.progress) || 0;
        const dur = Number(item.duration) || 0;
        const pct =
          dur > 30 ? Math.min(100, Math.round((prog / dur) * 100)) : prog > 30 ? 8 : 0;
        const meta =
          prog > 30
            ? `Lanjut ${formatTime(prog)}${dur > 30 ? ` / ${formatTime(dur)}` : ""}`
            : "Belum selesai";
        return `<button type="button" class="movies-recent-card" data-recent-url="${escapeHtml(item.url)}">
          <div class="movies-recent-poster-wrap">${posterInner}</div>
          <div class="movies-recent-body">
            <p class="movies-recent-name">${escapeHtml(item.title || "Film")}</p>
            <p class="movies-recent-meta">${escapeHtml(meta)}</p>
            ${pct > 0 ? `<div class="movies-recent-progress"><span style="width:${pct}%"></span></div>` : ""}
          </div>
        </button>`;
      })
      .join("");
    grid.querySelectorAll(".movies-recent-card").forEach((btn) => {
      btn.onclick = () => openMovieDetail(btn.dataset.recentUrl);
    });
    grid.querySelectorAll(".movies-recent-poster[data-poster]").forEach((img) => {
      applyPosterImg(img, img.dataset.poster, img.closest(".movies-recent-card")?.querySelector(".movies-recent-name")?.textContent || "");
    });
  }

  function saveProgress(url, seconds, extra = {}) {
    const key = getProgressKey(url);
    if (!key || !seconds || seconds < 30) return;
    try {
      localStorage.setItem(key, seconds.toString());
    } catch (e) {}
    touchRecentProgress(url, seconds, extra);
  }

  function loadProgress(url) {
    const keys = [];
    const key = getProgressKey(url);
    if (key) keys.push(key);
    const raw = (url || "").trim();
    if (raw) keys.push(`td-movie-progress:${raw}`);
    if (raw && !raw.endsWith("/")) keys.push(`td-movie-progress:${raw}/`);
    try {
      for (const k of keys) {
        const v = localStorage.getItem(k);
        if (v) return parseFloat(v) || 0;
      }
      return 0;
    } catch (e) {
      return 0;
    }
  }

  function clearProgress(url) {
    const key = getProgressKey(url);
    if (key) {
      try {
        localStorage.removeItem(key);
      } catch (e) {}
    }
    const list = loadRecent().filter((x) => x.url !== url);
    persistRecent(list);
    renderRecentMovies();
  }

  function isP2pEmbedUrl(url) {
    return /playerp2p|p2pplay\.(?:live|pro)/i.test(url || "");
  }

  function isKwikEmbedUrl(url) {
    return /kwik\.(?:cx|si)\//i.test(url || "");
  }

  function updateResumeBar(movieUrl) {
    const bar = $("#movie-resume-bar");
    const text = $("#movie-resume-text");
    if (!bar) return;
    const saved = loadProgress(movieUrl);
    if (saved > 30) {
      if (text) {
        text.textContent = `Progress tersimpan: lanjut dari ${formatTime(saved)}`;
      }
      show(bar);
    } else {
      hide(bar);
    }
  }

  function sendEmbedPlayerCommand(command, value) {
    postEmbedCommand($("#movie-player-embed"), command, value);
  }

  function seekPlaybackToSaved() {
    const saved = loadProgress(currentMovieUrl);
    if (saved < 30) return;
    const video = $("#movie-player");
    const embed = $("#movie-player-embed");
    if (embed && !embed.classList.contains("hidden")) {
      postEmbedCommand(embed, "seek", Math.floor(saved));
      triggerP2pAutoplay();
      setPlayerStatus(`Melanjutkan dari ${formatTime(saved)}…`);
      return;
    }
    if (video && !video.classList.contains("hidden") && video.readyState >= 1) {
      try {
        if (!video.duration || saved < video.duration - 30) {
          video.currentTime = saved;
        }
        video.play().catch(() => {});
        setPlayerStatus(`Melanjutkan dari ${formatTime(saved)}…`);
      } catch (e) {}
    }
  }

  function looksLikeJavCode(q) {
    const s = (q || "").trim().replace(/\s+/g, "");
    if (!s || s.length < 4) return false;
    let norm = s.toUpperCase();
    if (!norm.includes("-")) {
      const m = norm.match(/^([A-Z]{2,8})(\d{2,5}[A-Z]?)$/);
      if (m) norm = `${m[1]}-${m[2]}`;
    }
    return JAV_CODE_RE.test(norm);
  }

  function isCodeCatalogItem(m) {
    return m?.source === "code_catalog" || m?.type === "code_catalog";
  }

  function isTambukItem(m) {
    return m?.source === "tambuk" || /tambuk\.sbs/i.test(m?.url || "");
  }

  function isOtakudesuItem(m) {
    return m?.source === "otakudesu" || /otakudesu/i.test(m?.url || "");
  }

  function isNontonAnimeIDItem(m) {
    return (
      m?.source === "nontonanimeid" ||
      m?.source === "samehadaku" ||
      /nontonanimeid|samehadaku/i.test(m?.url || "")
    );
  }

  function isSamehadakuItem(m) {
    return isNontonAnimeIDItem(m);
  }

  function isDrakorKind(k = kind) {
    return k === "drakor";
  }

  function isAnimeKind(k = kind) {
    return k === "anime";
  }

  function isTambukKind(k = kind) {
    return isDrakorKind(k);
  }

  function isOtakudesuSource(source = currentMovieSource) {
    return source === "otakudesu";
  }

  function isNontonAnimeIDSource(source = currentMovieSource) {
    return source === "nontonanimeid" || source === "samehadaku";
  }

  function isSamehadakuSource(source = currentMovieSource) {
    return isNontonAnimeIDSource(source);
  }

  function isAnimeSource(source = currentMovieSource) {
    return isOtakudesuSource(source) || isNontonAnimeIDSource(source);
  }

  function isTambukSource(source = currentMovieSource) {
    return source === "tambuk";
  }

  function movieSourceFromItem(m) {
    if (isCodeCatalogItem(m)) return "code_catalog";
    if (isOtakudesuItem(m)) return "otakudesu";
    if (isNontonAnimeIDItem(m)) return "nontonanimeid";
    if (isTambukItem(m)) return "tambuk";
    return "lk21";
  }

  function movieApiBase(source) {
    if (source === "code_catalog") return "/api/movies/code-catalog";
    if (source === "otakudesu") return "/api/movies/otakudesu";
    if (source === "nontonanimeid" || source === "samehadaku") return "/api/movies/nontonanimeid";
    if (source === "tambuk") return "/api/movies/tambuk";
    return "/api/movies/lk21";
  }

  async function refreshCodeCatalogStatus() {
    try {
      const data = await api("/api/movies/code-catalog/status");
      codeCatalogStatus = data || codeCatalogStatus;
    } catch {
      codeCatalogStatus = { enabled: false, search_available: false };
    }
    return codeCatalogStatus;
  }

  function posterProxyUrl(poster, source = "lk21") {
    if (!poster) return "";
    if (poster.startsWith("/api/movies/code-catalog/poster")) return poster;
    if (poster.startsWith("/api/movies/tambuk/poster")) return poster;
    if (
      poster.startsWith("/api/movies/nontonanimeid/poster") ||
      poster.startsWith("/api/movies/samehadaku/poster") ||
      poster.startsWith("/api/movies/otakudesu/poster")
    ) {
      return poster;
    }
    if (source === "code_catalog" && poster.startsWith("/api/")) return poster;
    if (source === "otakudesu" || /otakudesu/i.test(poster)) {
      return `/api/movies/otakudesu/poster?u=${encodeURIComponent(poster)}`;
    }
    if (source === "nontonanimeid" || source === "samehadaku" || /nontonanimeid|samehadaku/i.test(poster)) {
      return `/api/movies/nontonanimeid/poster?u=${encodeURIComponent(poster)}`;
    }
    if (source === "tambuk" || /tambuk\.sbs/i.test(poster)) {
      return `/api/movies/tambuk/poster?u=${encodeURIComponent(poster)}`;
    }
    return `/api/movies/lk21/poster?u=${encodeURIComponent(poster)}`;
  }

  function applyPosterImg(img, poster, title, source = "lk21") {
    if (!img || !poster) {
      if (img) img.style.display = "none";
      return;
    }
    if (
      poster.startsWith("/api/movies/code-catalog/poster") ||
      poster.startsWith("/api/movies/tambuk/poster") ||
      poster.startsWith("/api/movies/nontonanimeid/poster") ||
      poster.startsWith("/api/movies/samehadaku/poster")
    ) {
      img.alt = title || "";
      img.style.display = "";
      img.referrerPolicy = "no-referrer";
      img.src = poster;
      return;
    }
    const direct = poster.startsWith("http") ? poster : "";
    const proxied = posterProxyUrl(poster, source);
    img.alt = title || "";
    img.style.display = "";
    img.referrerPolicy = "no-referrer";
    img.onerror = function () {
      if (img.dataset.fallback === "1") {
        img.style.display = "none";
        return;
      }
      img.dataset.fallback = "1";
      img.src = proxied;
    };
    img.src = direct || proxied;
  }

  /** URL player P2P — resume lewat hash resumeTime (tanpa round-trip API). */
  function buildP2pPlayerUrl(url, startSec = 0) {
    const raw = (url || "").trim();
    if (!raw) return raw;
    const start = Math.max(0, Math.floor(startSec || 0));
    const skip = /^(api|reportcurrenttime|resumetime|t|start)=/i;
    try {
      const u = new URL(raw);
      const parts = (u.hash || "")
        .replace(/^#/, "")
        .split("&")
        .map((p) => p.trim())
        .filter((p) => p && !skip.test(p));
      parts.push("api=all", "reportCurrentTime=1");
      if (start > 30) parts.push(`resumeTime=${start}`);
      u.hash = parts.join("&");
      return u.href;
    } catch {
      const base = raw.split("#")[0];
      const parts = ["api=all", "reportCurrentTime=1"];
      if (start > 30) parts.push(`resumeTime=${start}`);
      return `${base}#${parts.join("&")}`;
    }
  }

  function buildEmbedResumeUrl(url, startTime = 0) {
    if (!url || startTime < 30) return url;
    if (isP2pEmbedUrl(url)) return buildP2pPlayerUrl(url, startTime);
    const sec = Math.floor(startTime);
    try {
      const u = new URL(url);
      u.searchParams.set("t", String(sec));
      return u.toString();
    } catch {
      const sep = url.includes("?") ? "&" : "?";
      return `${url}${sep}t=${sec}`;
    }
  }

  function extractP2pPayloadTime(data) {
    if (!data || typeof data !== "object") return null;
    const t = data.currentTime;
    if (typeof t === "number" && !Number.isNaN(t)) return t;
    return null;
  }

  function stopEmbedProgressPoll() {
    if (embedProgressTimer) {
      clearInterval(embedProgressTimer);
      embedProgressTimer = null;
    }
  }

  function triggerP2pAutoplay() {
    sendEmbedPlayerCommand("play");
  }

  function postEmbedCommand(embed, command, value) {
    if (!embed?.contentWindow) return;
    const payload =
      value !== undefined && value !== null
        ? { command, value }
        : { command };
    let target = "*";
    try {
      if (embedBridge?.origin) target = embedBridge.origin;
      else target = new URL(embed.src, window.location.href).origin;
    } catch (e) {}
    try {
      embed.contentWindow.postMessage(payload, target);
    } catch (e) {}
    if (target !== "*") {
      try {
        embed.contentWindow.postMessage(payload, "*");
      } catch (e2) {}
    }
  }

  function startEmbedProgressPoll() {
    stopEmbedProgressPoll();
    embedProgressTimer = setInterval(() => {
      if (!embedBridge) {
        stopEmbedProgressPoll();
        return;
      }
      postEmbedCommand($("#movie-player-embed"), "getTime");
    }, EMBED_PROGRESS_POLL_MS);
  }

  function detachEmbedBridge() {
    stopEmbedProgressPoll();
    embedBridge = null;
  }

  function attachEmbedBridge(embedUrl, resumeInUrl = false) {
    let origin = "";
    try {
      origin = new URL(embedUrl).origin;
    } catch (e) {
      return;
    }
    embedBridge = {
      movieUrl: normalizeMovieUrl(currentMovieUrl),
      embedUrl,
      origin,
      ready: false,
      resumeInUrl: !!resumeInUrl,
    };
  }

  function handleP2pPlayerPayload(data) {
    if (!embedBridge || !data || typeof data !== "object") return;

    const meta = {
      title: currentMovie?.title || currentStream?.title,
      poster: currentMovie?.poster || "",
    };

    if (data.playerStatus === "Ready" && !embedBridge.ready) {
      embedBridge.ready = true;
      const saved = loadProgress(embedBridge.movieUrl);
      if (!embedBridge.resumeInUrl && saved > 30) {
        postEmbedCommand($("#movie-player-embed"), "seek", Math.floor(saved));
      }
      triggerP2pAutoplay();
      setPlayerStatus(
        saved > 30
          ? `Melanjutkan dari ${formatTime(saved)}…`
          : "Sedang diputar (P2P)…"
      );
      startEmbedProgressPoll();
      updateResumeBar(embedBridge.movieUrl);
    }

    if (data.playerStatus === "playing" || data.type === "STARTED") {
      setPlayerStatus("Sedang diputar (P2P).");
    }

    const t = extractP2pPayloadTime(data);
    if (t != null) {
      const dur = typeof data.duration === "number" ? data.duration : 0;
      if (t > 10) {
        markRecentOnPlayback();
        saveProgress(embedBridge.movieUrl, t, { ...meta, duration: dur });
        updateResumeBar(embedBridge.movieUrl);
        if (dur > 0 && t >= dur - 45) {
          clearProgress(embedBridge.movieUrl);
        }
      }
    }
  }

  function onEmbedWindowMessage(ev) {
    if (!embedBridge) return;
    if (ev.origin !== embedBridge.origin) return;
    handleP2pPlayerPayload(ev.data);
  }

  function seekEmbedToSaved() {
    const embed = $("#movie-player-embed");
    const saved = loadProgress(currentMovieUrl);
    if (!embed?.src || saved < 30) return;
    postEmbedCommand(embed, "seek", Math.floor(saved));
    setPlayerStatus(`Melanjutkan dari ${formatTime(saved)}…`);
  }

  function formatTime(sec) {
    if (!sec || sec < 0) return "0:00";
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${s.toString().padStart(2, "0")}`;
  }

  function show(el) {
    if (el) el.classList.remove("hidden");
  }
  function hide(el) {
    if (el) el.classList.add("hidden");
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function stopTsSegmentPlayer() {
    tsPlayerAbort = true;
    tsPlayerGen += 1;
    if (tsPlayerTimer) {
      clearTimeout(tsPlayerTimer);
      tsPlayerTimer = null;
    }
    const canvas = $("#movie-player-canvas");
    if (canvas) {
      canvas.remove();
    }
  }

  function destroyPlayer() {
    stopTsSegmentPlayer();
    const video = $("#movie-player");
    const embed = $("#movie-player-embed");
    if (embedBridge) {
      const embedEl = $("#movie-player-embed");
      postEmbedCommand(embedEl, "getTime");
    }
    detachEmbedBridge();
    if (hlsInstance) {
      hlsInstance.destroy();
      hlsInstance = null;
    }
    if (video) {
      video.pause();
      video.removeAttribute("src");
      video.load();
      show(video);
    }
    if (embed) {
      embed.removeAttribute("src");
      hide(embed);
    }
    hide($("#movie-kwik-open"));
    hide($("#movie-quality-wrap"));
    currentStream = null;
    if (isOtakudesuSource() && currentDownload?.save_supported) {
      rememberDownload(currentDownload);
    } else {
      setMovieSaveButtonEnabled(false);
    }
  }

  function setMovieSaveButtonEnabled(on) {
    const btn = $("#btn-movie-save-telegram");
    if (btn) btn.disabled = !on;
  }

  function isBloggerSaveable(data, iframeUrl = "") {
    if (data?.save_provider === "blogger") return true;
    const url = iframeUrl || data?.embed_url || data?.iframe || "";
    return /blogger\.com\/video/i.test(url);
  }

  function isHydrxSaveable(data, iframeUrl = "") {
    if (data?.save_supported === true || data?.save_provider === "hydrx") return true;
    const url = iframeUrl || data?.embed_url || data?.iframe || "";
    return /abyssplayer\.com/i.test(url) || /hydrax\.php/i.test(url);
  }

  function buildSaveTitle() {
    const base = (currentMovie?.title || "film").trim() || "film";
    const episodes = currentMovie?.episodes;
    if (!Array.isArray(episodes) || !episodes.length) return base;
    const ep =
      episodes.find((item) => item.index === currentEpisodeIndex) || episodes[0];
    if (!ep) return base;
    const label = (ep.label || `Episode ${ep.number || ep.index + 1}`).trim();
    return `${base} - ${label}`;
  }

  function streamCanSaveToTelegram(data, iframeUrl = "") {
    if (data?.save_supported === true) return true;
    if (data?.m3u8 || data?.mp4) return true;
    return isHydrxSaveable(data, iframeUrl) || isBloggerSaveable(data, iframeUrl);
  }

  function rememberStream(data, iframeUrl) {
    const title = buildSaveTitle();
    const embedOnly =
      data?.player_mode === "embed" ||
      (!data?.m3u8 && !!(data?.embed_url || data?.iframe || iframeUrl));
    const saveable = streamCanSaveToTelegram(data, iframeUrl);
    if (data?.m3u8) {
      currentStream = {
        m3u8: data.m3u8,
        referer: data.referer || data.iframe || iframeUrl || "",
        iframe_url: iframeUrl || "",
        title,
        embedOnly: false,
        movie_url: currentMovie?.url || "",
        save_provider: data?.save_provider || "",
      };
      setMovieSaveButtonEnabled(true);
      return;
    }
    if ((iframeUrl || data?.mp4) && saveable) {
      currentStream = {
        m3u8: "",
        referer:
          data?.referer ||
          currentMovie?.url ||
          data?.iframe ||
          iframeUrl ||
          "",
        iframe_url: data?.embed_url || data?.iframe || iframeUrl || "",
        mp4: data?.mp4 || "",
        mp4_play_url: data?.mp4_play_url || "",
        title,
        embedOnly,
        movie_url: currentMovie?.url || "",
        save_provider:
          data?.save_provider ||
          (isHydrxSaveable(data, iframeUrl)
            ? "hydrx"
            : isBloggerSaveable(data, iframeUrl)
              ? "blogger"
              : ""),
      };
      setMovieSaveButtonEnabled(true);
      return;
    }
    if (saveable && (isHydrxSaveable(data, iframeUrl) || isBloggerSaveable(data, iframeUrl))) {
      currentStream = {
        m3u8: "",
        referer: currentMovie?.url || data?.referer || iframeUrl || "",
        iframe_url: data?.embed_url || data?.iframe || iframeUrl || "",
        title,
        embedOnly,
        movie_url: currentMovie?.url || "",
        save_provider:
          data?.save_provider ||
          (isHydrxSaveable(data, iframeUrl)
            ? "hydrx"
            : isBloggerSaveable(data, iframeUrl)
              ? "blogger"
              : ""),
      };
      setMovieSaveButtonEnabled(true);
      return;
    }
    if (isOtakudesuSource() && currentDownload?.save_supported) {
      rememberDownload(currentDownload);
      return;
    }
    currentStream = null;
    setMovieSaveButtonEnabled(false);
  }

  function hlsProxyUrl(m3u8, referer) {
    const q = new URLSearchParams({ u: m3u8, r: referer || "" });
    const base = movieApiBase(currentMovieSource);
    return `${base}/hls?${q}`;
  }

  /** MP4 hasil resolve P2P — CDN butuh Referer player, wajib lewat proxy same-origin. */
  function mediaProxyUrl(mp4, referer) {
    if (!mp4) return "";
    if (isSameOriginUrl(mp4)) return mp4;
    const q = new URLSearchParams({ u: mp4, r: referer || "" });
    return `/api/movies/lk21/media?${q}`;
  }

  function playDirectMp4(video, mp4, referer, pageUrl, playUrl = "") {
    detachEmbedBridge();
    show(video);
    hide($("#movie-kwik-open"));
    const embed = $("#movie-player-embed");
    if (embed) {
      embed.removeAttribute("src");
      hide(embed);
    }

    const src = playUrl || mediaProxyUrl(mp4, referer);
    if (currentStream) currentStream.mp4_play_url = src;
    video.preload = "auto";
    video.playsInline = true;
    video.src = src;
    video.referrerPolicy = "no-referrer";
    attachVideoResume(video, currentMovieUrl);

    video.addEventListener(
      "playing",
      () => setPlayerStatus("Sedang diputar."),
      { once: true }
    );
    video.addEventListener(
      "error",
      () => {
        setPlayerStatus(
          "Gagal memuat stream. Refresh halaman atau login ulang lalu coba lagi."
        );
      },
      { once: true }
    );
    video.addEventListener(
      "canplay",
      () => {
        video.play().catch(() => {});
        setPlayerStatus("Sedang diputar.");
      },
      { once: true }
    );
    video.load();
    setPlayerStatus("Memuat stream…");
  }

  function isSameOriginUrl(url) {
    try {
      return new URL(url, window.location.origin).origin === window.location.origin;
    } catch {
      return false;
    }
  }

  function canPlayNativeHls(video) {
    const v = video || document.createElement("video");
    return !!(
      v.canPlayType("application/vnd.apple.mpegurl") ||
      v.canPlayType("application/x-mpegURL")
    );
  }

  /** Safari / iOS — native HLS (mobile yang kamu bilang normal). */
  function shouldPreferNativeHls() {
    if (!canPlayNativeHls()) return false;
    if (!window.Hls || !window.Hls.isSupported()) return true;
    const ua = navigator.userAgent;
    if (/iPhone|iPad|iPod/i.test(ua)) return true;
    if (/Macintosh/i.test(ua) && /Safari/i.test(ua) && !/Chrome|Chromium|Edg/i.test(ua)) return true;
    return false;
  }

  /** Desktop pointer halus — hls.js sering gagal di segment CDN; siapkan embed. */
  function isDesktopLike() {
    return (
      window.matchMedia("(pointer: fine)").matches &&
      window.innerWidth >= 768 &&
      !/iPhone|iPad|Android/i.test(navigator.userAgent)
    );
  }

  /** Sumber katalog tidak punya player embed — halaman sumber tidak bisa di-iframe. */
  function canEmbedPageFallback() {
    return currentMovieSource !== "code_catalog";
  }

  function codeCatalogPlaybackHint() {
    return "Gagal memutar stream — coba refresh halaman. Cookies Cloudflare hanya diperlukan jika server tidak bisa memuat detail film.";
  }

  function absStreamUrl(pathOrUrl) {
    const u = (pathOrUrl || "").trim();
    if (!u) return "";
    if (u.startsWith("http://") || u.startsWith("https://")) return u;
    if (u.startsWith("/")) return `${window.location.origin}${u}`;
    return u;
  }

  async function fetchProxiedText(pathOrUrl) {
    const url = absStreamUrl(pathOrUrl);
    const r = await fetch(url, { credentials: "same-origin" });
    if (!r.ok) throw new Error(`Gagal memuat playlist (${r.status})`);
    return r.text();
  }

  function pickCatalogVariantUrl(masterText) {
    const lines = masterText.split("\n");
    let best = { score: -1, url: "" };
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].trim();
      if (!line.startsWith("#EXT-X-STREAM-INF")) continue;
      const next = (lines[i + 1] || "").trim();
      if (!next || next.startsWith("#")) continue;
      let score = 0;
      const res = line.match(/RESOLUTION=(\d+)x(\d+)/i);
      if (res) score = parseInt(res[2], 10) || 0;
      const bw = line.match(/BANDWIDTH=(\d+)/i);
      if (bw) score += (parseInt(bw[1], 10) || 0) / 100000;
      if (score >= best.score) best = { score, url: next };
    }
    return best.url;
  }

  function parseTsSegments(playlistText) {
    const segments = [];
    let duration = 4;
    for (const line of playlistText.split("\n")) {
      const t = line.trim();
      if (!t) continue;
      if (t.startsWith("#EXTINF:")) {
        duration = parseFloat(t.split(":")[1]) || 4;
      } else if (!t.startsWith("#")) {
        segments.push({ duration, url: t });
      }
    }
    return segments;
  }

  function segmentIndexForTime(segments, seconds) {
    let t = 0;
    for (let i = 0; i < segments.length; i++) {
      if (t + segments[i].duration > seconds) return i;
      t += segments[i].duration;
    }
    return 0;
  }

  async function playTsSegmentHls(video, m3u8, referer) {
    const wrap = video?.parentElement;
    if (!wrap) throw new Error("Pemutar tidak tersedia");
    stopTsSegmentPlayer();
    const gen = ++tsPlayerGen;
    tsPlayerAbort = false;

    hide($("#movie-player-embed"));
    hide(video);

    let canvas = $("#movie-player-canvas");
    if (!canvas) {
      canvas = document.createElement("canvas");
      canvas.id = "movie-player-canvas";
      canvas.className = "movies-player-canvas";
      wrap.appendChild(canvas);
    }
    show(canvas);

    const resizeCanvas = () => {
      const rect = wrap.getBoundingClientRect();
      canvas.width = Math.max(320, Math.floor(rect.width || 640));
      canvas.height = Math.max(180, Math.floor((rect.width || 640) * 9) / 16);
    };
    resizeCanvas();

    setPlayerStatus("Memuat stream…");
    const masterText = await fetchProxiedText(hlsProxyUrl(m3u8, referer));
    if (gen !== tsPlayerGen || tsPlayerAbort) return;

    const variantPath = pickCatalogVariantUrl(masterText);
    if (!variantPath) throw new Error("Kualitas stream tidak ditemukan");

    const variantText = await fetchProxiedText(variantPath);
    if (gen !== tsPlayerGen || tsPlayerAbort) return;

    const segments = parseTsSegments(variantText);
    if (!segments.length) throw new Error("Segment video kosong");

    const ctx = canvas.getContext("2d");
    let idx = 0;
    const saved = loadProgress(currentMovieUrl);
    if (saved > 10) idx = segmentIndexForTime(segments, saved);

    let elapsed = segments.slice(0, idx).reduce((a, s) => a + s.duration, 0);
    let lastSave = 0;
    let recentMarkedForCanvas = false;

    const drawFrame = async () => {
      if (gen !== tsPlayerGen || tsPlayerAbort) return;
      const seg = segments[idx % segments.length];
      const img = new Image();
      img.referrerPolicy = "no-referrer";
      await new Promise((resolve, reject) => {
        img.onload = () => resolve();
        img.onerror = () => reject(new Error("Gagal memuat frame video"));
        img.src = absStreamUrl(seg.url);
      });
      if (gen !== tsPlayerGen || tsPlayerAbort) return;

      resizeCanvas();
      const cw = canvas.width;
      const ch = canvas.height;
      const ir = img.width / img.height;
      const cr = cw / ch;
      let dw;
      let dh;
      let dx;
      let dy;
      if (ir > cr) {
        dh = ch;
        dw = ch * ir;
        dx = (cw - dw) / 2;
        dy = 0;
      } else {
        dw = cw;
        dh = cw / ir;
        dx = 0;
        dy = (ch - dh) / 2;
      }
      ctx.fillStyle = "#000";
      ctx.fillRect(0, 0, cw, ch);
      ctx.drawImage(img, dx, dy, dw, dh);
      if (!recentMarkedForCanvas) {
        recentMarkedForCanvas = true;
        markRecentOnPlayback();
      }
      setPlayerStatus("Sedang diputar.");

      if (currentMovieUrl && elapsed - lastSave >= 8) {
        saveProgress(currentMovieUrl, elapsed);
        lastSave = elapsed;
      }

      idx += 1;
      if (idx >= segments.length) idx = 0;
      elapsed += seg.duration || 4;
      tsPlayerTimer = setTimeout(() => {
        drawFrame().catch((e) => setPlayerStatus(e.message || codeCatalogPlaybackHint()));
      }, Math.max(200, (seg.duration || 4) * 1000));
    };

    await drawFrame();
  }

  function playNativeHls(video, streamUrl, embedUrl) {
    video.removeAttribute("crossorigin");
    const onErr = () => {
      if (embedUrl && canEmbedPageFallback()) {
        destroyPlayer();
        void showEmbed(embedUrl);
        return;
      }
      setPlayerStatus(canEmbedPageFallback() ? "Gagal memutar (native HLS)." : codeCatalogPlaybackHint());
    };
    video.addEventListener("error", onErr, { once: true });
    video.src = streamUrl;
    attachVideoResume(video, currentMovieUrl);
    video.addEventListener(
      "loadedmetadata",
      () => {
        const saved = loadProgress(currentMovieUrl);
        applyVideoResumeSeek(video, saved);
        video.play().catch(() => {});
        setPlayerStatus(
          saved > 30 ? `Melanjutkan dari ${formatTime(saved)}…` : "Sedang diputar."
        );
      },
      { once: true }
    );
  }

  function attachHlsJs(video, streamUrl, embedUrl, referer, rawM3u8) {
    if (!window.Hls?.isSupported()) return false;

    let triedDirect = false;
    const startPosition = hlsResumeStartSec();
    hlsInstance = new window.Hls({
      enableWorker: true,
      lowLatencyMode: false,
      startPosition,
      xhrSetup: (xhr, url) => {
        if (isSameOriginUrl(url)) xhr.withCredentials = true;
      },
    });

    const tryDirect = () => {
      if (!rawM3u8 || triedDirect) return false;
      triedDirect = true;
      hlsInstance.destroy();
      hlsInstance = new window.Hls({
        enableWorker: true,
        startPosition: hlsResumeStartSec(),
        xhrSetup: (xhr, url) => {
          if (isSameOriginUrl(url)) xhr.withCredentials = true;
        },
      });
      hlsInstance.loadSource(rawM3u8);
      hlsInstance.attachMedia(video);
      bindHlsEvents(video, embedUrl, referer, rawM3u8, true);
      setPlayerStatus("Mencoba stream langsung…");
      return true;
    };

    hlsInstance.loadSource(streamUrl);
    hlsInstance.attachMedia(video);
    attachVideoResume(video, currentMovieUrl);
    bindHlsEvents(video, embedUrl, referer, rawM3u8, false, tryDirect);
    return true;
  }

  function bindHlsEvents(video, embedUrl, referer, rawM3u8, directOnly, tryDirectFn) {
    hlsInstance.on(window.Hls.Events.MANIFEST_PARSED, () => {
      video.play().catch(() => {});
      setPlayerStatus(directOnly ? "Sedang diputar (langsung)." : "Sedang diputar (HLS).");
    });
    hlsInstance.on(window.Hls.Events.ERROR, (_, data) => {
      if (!data?.fatal) return;
      if (!directOnly && tryDirectFn && tryDirectFn()) return;
      if (embedUrl && canEmbedPageFallback()) {
        destroyPlayer();
        void showEmbed(embedUrl);
        return;
      }
      setPlayerStatus(
        canEmbedPageFallback() ? "Gagal memutar — coba server TurboVIP." : codeCatalogPlaybackHint()
      );
    });
  }

  function resolvedEmbedUrl(streamPayload, pageUrl = "") {
    const resolved = (
      streamPayload?.embed_url ||
      streamPayload?.iframe ||
      streamPayload?.m3u8 ||
      pageUrl ||
      ""
    ).trim();
    if (!resolved) return "";
    const low = resolved.toLowerCase();
    const pageLow = (pageUrl || "").toLowerCase();
    if (
      pageLow &&
      /nontonanimeid|samehadaku/i.test(pageLow) &&
      resolved === pageUrl &&
      !/blogger\.com|abyssplayer|playeriframe|m3u8|\.mp4/i.test(low)
    ) {
      return "";
    }
    return resolved;
  }

  function showKwikExternal(url) {
    const video = $("#movie-player");
    const embed = $("#movie-player-embed");
    hide(video);
    detachEmbedBridge();
    if (embed) {
      embed.removeAttribute("src");
      hide(embed);
    }
    let btn = $("#movie-kwik-open");
    if (!btn) {
      btn = document.createElement("button");
      btn.id = "movie-kwik-open";
      btn.type = "button";
      btn.className = "btn primary sm";
      btn.style.marginTop = "0.5rem";
      const status = $("#movie-player-status");
      if (status?.parentNode) status.parentNode.insertBefore(btn, status.nextSibling);
    }
    btn.textContent = "Buka player ENG SUB (kwik.cx)";
    btn.onclick = () => window.open(url, "_blank", "noopener,noreferrer");
    show(btn);
    setPlayerStatus(
      "Server ENG SUB memakai kwik.cx — tidak bisa embed di sini. Klik tombol di bawah atau pilih INDO SUB."
    );
  }

  function showEmbed(url, startTime = 0) {
    const video = $("#movie-player");
    const embed = $("#movie-player-embed");
    if (!embed || !url) return;
    if (isKwikEmbedUrl(url)) {
      showKwikExternal(url);
      return;
    }
    hide($("#movie-kwik-open"));
    hide(video);
    detachEmbedBridge();
    const saved = Math.max(
      0,
      Math.floor(startTime > 0 ? startTime : loadProgress(currentMovieUrl))
    );
    const p2p = isP2pEmbedUrl(url);
    const embedSrc = p2p ? buildP2pPlayerUrl(url, saved) : buildEmbedResumeUrl(url, saved);
    embed.src = embedSrc;
    attachEmbedBridge(url, p2p && saved > 30);
    setPlayerStatus(
      saved > 30
        ? `Melanjutkan dari ${formatTime(saved)}…`
        : p2p
          ? "Memuat player P2P…"
          : "Memutar via player embed."
    );
    show(embed);
    updateResumeBar(currentMovieUrl);
  }

  function setPlayerStatus(text) {
    const el = $("#movie-player-status");
    if (el) el.textContent = text || "";
  }

  function hlsResumeStartSec() {
    const saved = loadProgress(currentMovieUrl);
    return saved > 30 ? saved : -1;
  }

  function applyVideoResumeSeek(video, saved) {
    if (!video || saved <= 30) return;
    const seek = () => {
      if (!video.duration || saved >= video.duration - 5) return;
      try {
        if (typeof video.fastSeek === "function") video.fastSeek(saved);
        else video.currentTime = saved;
      } catch (e) {}
    };
    if (video.readyState >= 1) seek();
    else video.addEventListener("loadedmetadata", seek, { once: true });
  }

  function attachVideoResume(video, movieUrl) {
    if (!video || !movieUrl) return;
    video.addEventListener("playing", () => markRecentOnPlayback(), { once: true });
    const saved = loadProgress(movieUrl);
    applyVideoResumeSeek(video, saved);

    let saveT;
    const doSave = () => {
      if (video.currentTime > 30) {
        saveProgress(movieUrl, video.currentTime);
      }
    };
    video.addEventListener("timeupdate", () => {
      if (saveT) clearTimeout(saveT);
      saveT = setTimeout(doSave, 5000);
    }, { passive: true });

    video.addEventListener("pause", doSave);
    video.addEventListener("ended", () => clearProgress(movieUrl));

    const onVis = () => {
      if (document.visibilityState === "hidden" && video.currentTime > 30) {
        saveProgress(movieUrl, video.currentTime, {
          title: currentMovie?.title,
          poster: currentMovie?.poster,
          duration: video.duration || 0,
        });
      }
    };
    document.addEventListener("visibilitychange", onVis);
    video.addEventListener(
      "ended",
      () => document.removeEventListener("visibilitychange", onVis),
      { once: true }
    );
  }

  function showBrowse() {
    hide($("#movies-detail"));
    show($("#movies-browse"));
    hide($("#btn-movies-back"));
    hide($("#btn-movies-back-detail"));
    hide($("#movies-loading"));
    hide($("#movies-error"));
    episodeDownloads = [];
    currentDownload = null;
    if (typeof window.updateTopbarForPanel === "function") window.updateTopbarForPanel();
    destroyPlayer();
    renderRecentMovies();
    // clear movie deep link when going back to list
    currentMovieUrl = null;
    currentMovie = null;
    if (typeof window.syncAppState === "function") {
      window.syncAppState();
    }
  }

  function showDetailView() {
    show($("#movies-detail"));
    hide($("#movies-browse"));
    show($("#btn-movies-back"));
    show($("#btn-movies-back-detail"));
  }

  function syncTabs() {
    document.querySelectorAll("[data-movie-kind]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.movieKind === kind && !searchQuery);
    });
  }

  function renderMovies(data) {
    const grid = $("#movies-grid");
    const loading = $("#movies-loading");
    const err = $("#movies-error");
    const pag = $("#movies-pagination");
    const hint = $("#movies-list-hint");

    hide(loading);
    hide(err);
    lastList = data;

    const movies = data.movies || [];
    if (!movies.length) {
      hide(grid);
      hide(pag);
      if (hint) {
        hint.textContent = searchQuery
          ? `Tidak ada hasil untuk “${searchQuery}”.`
          : "Tidak ada film di kategori ini.";
      }
      return;
    }

    if (hint) {
      const totalPages = data.total_pages || 1;
      const curPage = data.page || page;
      const label =
        data.source === "otakudesu" ||
        data.source === "nontonanimeid" ||
        data.source === "samehadaku"
          ? "anime"
          : data.source === "tambuk"
            ? "drakor"
            : "film";
      let t = `${data.count || movies.length} ${label}`;
      if (totalPages > 1) {
        t += ` · hal ${curPage}/${totalPages}`;
      }
      if (data.fallback && data.fallback_query) {
        t += ` (sumber alternatif: “${data.fallback_query}”)`;
      } else if (searchQuery) {
        t += ` · pencarian “${searchQuery}”`;
      } else if (data.source === "otakudesu") {
        t += " · OtakuDesu";
      } else if (data.source === "nontonanimeid" || data.source === "samehadaku") {
        t += " · NontonAnimeID";
      } else if (data.source === "tambuk") {
        t += " · Tambuk.sbs";
      }
      hint.textContent = t;
    }

    show(grid);
    grid.innerHTML = movies
      .map((m) => {
        const posterInner = m.poster
          ? `<img class="movie-card-poster" data-poster="${escapeHtml(m.poster)}" src="${escapeHtml(m.poster)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
          : `<div class="movie-card-poster movie-card-poster--empty" aria-hidden="true"></div>`;
        const meta = [m.year, m.quality, m.rating ? `★ ${m.rating}` : null, m.duration]
          .filter(Boolean)
          .join(" · ");
        const src = movieSourceFromItem(m);
        const codeBadge = m.video_code
          ? `<span class="movie-card-badge">${escapeHtml(m.video_code)}</span>`
          : "";
        return `<button type="button" class="movie-card" data-movie-url="${escapeHtml(m.url)}" data-movie-source="${src}">
          <div class="movie-card-poster-wrap">
            ${posterInner}
            ${codeBadge}
          </div>
          <div class="movie-card-body">
            <p class="movie-card-title">${escapeHtml(m.title || "—")}</p>
            <p class="movie-card-meta">${escapeHtml(meta)}</p>
          </div>
        </button>`;
      })
      .join("");

    grid.querySelectorAll(".movie-card").forEach((card) => {
      card.onclick = () =>
        openMovieDetail(card.dataset.movieUrl, card.dataset.movieSource || "lk21");
    });
    grid.querySelectorAll(".movie-card-poster[data-poster]").forEach((img) => {
      applyPosterImg(img, img.dataset.poster, "");
    });

    moviesListMeta = {
      page: data.page || page,
      total_pages: data.total_pages || 1,
      total: data.total ?? data.count ?? movies.length,
      per_page: data.per_page || MOVIES_PER_PAGE,
    };
    page = moviesListMeta.page;
    renderMoviesPagination();
  }

  function renderMoviesPagination() {
    const pag = $("#movies-pagination");
    const pages = moviesListMeta.total_pages || 1;
    const cur = moviesListMeta.page || 1;
    const total = moviesListMeta.total || 0;

    if (!pag || pages <= 1) {
      hide(pag);
      if (pag) pag.innerHTML = "";
      return;
    }

    const build = window.buildPaginationHtml;
    if (typeof build !== "function") {
      hide(pag);
      return;
    }

    const isMobile = window.innerWidth < 640;
    pag.innerHTML = build(cur, pages, isMobile);
    show(pag);
    pag.querySelectorAll("[data-page]").forEach((btn) => {
      btn.onclick = () => {
        const p = parseInt(btn.dataset.page, 10);
        if (!Number.isFinite(p) || p === page) return;
        page = p;
        loadList();
        if (typeof window.syncAppState === "function") window.syncAppState();
      };
    });
  }

  function stopMoviesListFetch() {
    if (moviesListAbort) {
      moviesListAbort.abort();
      moviesListAbort = null;
    }
  }

  async function loadList() {
    const reqId = ++moviesListGen;
    stopMoviesListFetch();
    moviesListAbort = new AbortController();
    const signal = moviesListAbort.signal;

    const loading = $("#movies-loading");
    const err = $("#movies-error");
    const grid = $("#movies-grid");
    hide(err);
    if (!lastList || !grid?.innerHTML) {
      hide(grid);
      hide($("#movies-pagination"));
      show(loading);
      if (loading) loading.textContent = "Memuat film…";
    }

    try {
      let data;
      const useCodeCatalogSearch =
        searchQuery &&
        codeCatalogStatus.search_available &&
        looksLikeJavCode(searchQuery);
      const useOtakudesu =
        isAnimeKind() || (searchQuery && isOtakudesuSource(currentMovieSource));
      const useNontonAnimeID =
        searchQuery && isNontonAnimeIDSource(currentMovieSource);
      const useTambuk = isDrakorKind() || (searchQuery && isTambukSource(currentMovieSource));
      const listParams = new URLSearchParams({
        page: String(page),
        per_page: String(MOVIES_PER_PAGE),
      });
      if (useCodeCatalogSearch) {
        listParams.set("q", searchQuery);
        data = await api(`/api/movies/code-catalog/search?${listParams}`, { signal });
        if (loading) loading.textContent = "Mencari…";
      } else if (searchQuery && useOtakudesu) {
        listParams.set("q", searchQuery);
        data = await api(`/api/movies/otakudesu/search?${listParams}`, { signal });
        if (loading) loading.textContent = "Mencari anime…";
      } else if (searchQuery && useNontonAnimeID) {
        listParams.set("q", searchQuery);
        data = await api(`/api/movies/nontonanimeid/search?${listParams}`, { signal });
        if (loading) loading.textContent = "Mencari anime…";
      } else if (searchQuery && useTambuk) {
        listParams.set("q", searchQuery);
        data = await api(`/api/movies/tambuk/search?${listParams}`, { signal });
        if (loading) loading.textContent = "Mencari drakor…";
      } else if (searchQuery) {
        listParams.set("q", searchQuery);
        data = await api(`/api/movies/lk21/search?${listParams}`, { signal });
      } else if (isAnimeKind()) {
        data = await api(`/api/movies/otakudesu/list?${listParams}`, { signal });
        if (loading) loading.textContent = "Memuat anime…";
      } else if (isDrakorKind()) {
        listParams.set("kind", "drakor");
        data = await api(`/api/movies/tambuk/list?${listParams}`, { signal });
        if (loading) loading.textContent = "Memuat drakor…";
      } else {
        listParams.set("kind", kind);
        data = await api(`/api/movies/lk21/list?${listParams}`, { signal });
      }
      if (reqId !== moviesListGen) {
        hide(loading);
        return;
      }
      if ($("#panel-movies")?.classList.contains("hidden")) {
        hide(loading);
        return;
      }
      renderMovies(data);
      if (typeof window.syncAppState === "function") window.syncAppState();
    } catch (e) {
      if (e.name === "AbortError" || reqId !== moviesListGen) {
        hide(loading);
        return;
      }
      hide(loading);
      show(err);
      if (err) err.textContent = e.message || "Gagal memuat daftar film.";
    }
  }

  function onHide() {
    stopMoviesListFetch();
    moviesListGen += 1;
    movieDetailGen += 1;
    currentMovieUrl = null;
    hide($("#movies-loading"));
    hide($("#movies-error"));
    stopEmbedProgressPoll();
    destroyPlayer();
  }

  function setActiveEpisodeButton(box, activeIndex) {
    if (!box) return;
    box.querySelectorAll(".movies-episode-btn").forEach((btn) => {
      const isActive = Number(btn.dataset.episodeIndex) === activeIndex;
      btn.classList.toggle("active", isActive);
      btn.setAttribute("aria-pressed", isActive ? "true" : "false");
      if (isActive) btn.setAttribute("aria-current", "true");
      else btn.removeAttribute("aria-current");
    });
  }

  function serverButtonLabel(server) {
    const base = server?.label || server?.provider || "Server";
    const quality = (server?.quality || "").trim();
    if (quality && !base.toLowerCase().includes(quality.toLowerCase())) {
      return `${base} (${quality})`;
    }
    return base;
  }

  function rememberDownload(item) {
    if (!item?.url) {
      currentDownload = null;
      if (isOtakudesuSource()) setMovieSaveButtonEnabled(false);
      return;
    }
    currentDownload = item;
    currentStream = {
      mp4: item.direct_mp4 || "",
      referer: item.referer || currentMovieUrl || "",
      iframe_url: "",
      download_url: item.url,
      title: buildSaveTitle(),
      embedOnly: false,
      movie_url: item.referer || currentMovieUrl || "",
      save_provider: "otakudesu_download",
      quality: item.quality || "",
      host: item.label || "",
    };
    setMovieSaveButtonEnabled(!!item.save_supported);
  }

  function pickAutoDownload(downloads) {
    if (!Array.isArray(downloads) || !downloads.length) return null;
    const saveable = downloads.filter((d) => d?.save_supported);
    if (!saveable.length) return null;
    const hd =
      saveable.find((d) => /720p/i.test(d.quality || "")) ||
      saveable.find((d) => /1080p/i.test(d.quality || "")) ||
      saveable[0];
    return hd;
  }

  async function loadEpisodeDownloads(ep) {
    if (!isOtakudesuSource() || !ep?.url) {
      episodeDownloads = [];
      currentDownload = null;
      return [];
    }
    try {
      const q = new URLSearchParams({ url: ep.url });
      const data = await api(`${movieApiBase(currentMovieSource)}/episode-downloads?${q}`);
      episodeDownloads = Array.isArray(data?.downloads) ? data.downloads : [];
      return episodeDownloads;
    } catch (e) {
      episodeDownloads = [];
      return [];
    }
  }

  function applyEpisodeDownloads(downloads) {
    const list = Array.isArray(downloads) ? downloads.filter((d) => d?.save_supported) : [];
    episodeDownloads = list;
    rememberDownload(pickAutoDownload(list));
  }

  function updateQualityPicker(qualities, selectedQuality = "") {
    const wrap = $("#movie-quality-wrap");
    const sel = $("#movie-quality");
    if (!wrap || !sel) return;
    const list = Array.isArray(qualities) ? qualities.filter((q) => q?.mp4) : [];
    if (list.length < 2) {
      hide(wrap);
      sel.innerHTML = "";
      return;
    }
    show(wrap);
    sel.innerHTML = list
      .map((q) => {
        const label = escapeHtml(q.quality || q.label || "Auto");
        const selected =
          selectedQuality && (q.quality === selectedQuality || q.label === selectedQuality)
            ? " selected"
            : "";
        return `<option value="${escapeHtml(q.mp4)}" data-quality="${label}"${selected}>${label}</option>`;
      })
      .join("");
    sel.onchange = () => {
      const video = $("#movie-player");
      const mp4 = sel.value || "";
      const quality = sel.selectedOptions?.[0]?.dataset?.quality || "";
      if (!video || !mp4) return;
      const referer = currentStream?.referer || currentMovieUrl || "";
      playDirectMp4(video, mp4, referer, currentStream?.iframe_url || currentMovieUrl || "", "");
      if (currentStream) currentStream.quality = quality;
      setPlayerStatus(`Memutar ${quality || "stream"}…`);
    };
  }

  async function loadEpisodeServers(ep) {
    if (!ep?.url) return ep?.servers || [];
    const cached = Array.isArray(ep.servers) ? ep.servers : [];
    if (cached.length > 1) return cached;
    setPlayerStatus("Memuat daftar server…");
    try {
      const q = new URLSearchParams({ url: ep.url });
      const data = await api(`${movieApiBase(currentMovieSource)}/episode-servers?${q}`);
      const servers = Array.isArray(data?.servers) ? data.servers : [];
      if (servers.length) {
        ep.servers = servers;
        if (Array.isArray(currentMovie?.episodes)) {
          const idx = currentMovie.episodes.findIndex((item) => item.url === ep.url);
          if (idx >= 0) currentMovie.episodes[idx].servers = servers;
        }
      }
      return servers.length ? servers : cached;
    } catch (e) {
      return cached;
    }
  }

  function renderEpisodes(episodes, selectedIndex = 0) {
    const wrap = $("#movie-episodes-wrap");
    const box = $("#movie-episodes");
    if (!wrap || !box) return;
    const list = Array.isArray(episodes) ? episodes : [];
    if (!list.length) {
      hide(wrap);
      box.innerHTML = "";
      currentEpisodeIndex = 0;
      return;
    }
    show(wrap);
    const label = wrap.querySelector(".movies-server-label");
    if (label) {
      label.textContent = `Pilih episode (${list.length})`;
    }
    const activeIndex = list.some((ep) => ep.index === selectedIndex)
      ? selectedIndex
      : list[0].index;
    currentEpisodeIndex = activeIndex;
    const activeEp = list.find((ep) => ep.index === activeIndex) || list[0];
    box.innerHTML = list
      .map((ep) => {
        const isActive = ep.index === activeIndex;
        const num = escapeHtml(ep.number || ep.label || String(ep.index + 1));
        return `<button type="button" class="btn ghost sm movies-episode-btn${
          isActive ? " active" : ""
        }" data-episode-index="${ep.index}" aria-pressed="${isActive ? "true" : "false"}"${
          isActive ? ' aria-current="true"' : ""
        } title="Episode ${num}">${num}</button>`;
      })
      .join("");
    setActiveEpisodeButton(box, activeIndex);
    box.querySelectorAll(".movies-episode-btn").forEach((btn) => {
      btn.onclick = async () => {
        const epIndex = Number(btn.dataset.episodeIndex);
        const ep = list.find((x) => x.index === epIndex);
        if (!ep) return;
        currentEpisodeIndex = epIndex;
        setActiveEpisodeButton(box, epIndex);
        const [servers, downloads] = await Promise.all([
          loadEpisodeServers(ep),
          loadEpisodeDownloads(ep),
        ]);
        renderServers(servers);
        applyEpisodeDownloads(downloads);
      };
    });
    void (async () => {
      const [servers, downloads] = await Promise.all([
        loadEpisodeServers(activeEp),
        loadEpisodeDownloads(activeEp),
      ]);
      renderServers(servers);
      applyEpisodeDownloads(downloads);
    })();
  }

  function pickAutoPlayServer(servers) {
    if (!Array.isArray(servers) || !servers.length) return null;
    const hd = servers.find((s) => /720p|1080p/i.test(`${s?.quality || ""} ${s?.label || ""}`));
    if (hd) return hd;
    const blogger = servers.find((s) => /blogger\.com/i.test(s?.iframe_url || ""));
    if (blogger) return blogger;
    const hls = servers.find((s) => (s?.m3u8 || "").trim());
    if (hls) return hls;
    const mp4 = servers.find((s) => (s?.mp4 || "").trim());
    if (mp4) return mp4;
    const embed = servers.find(
      (s) => (s?.iframe_url || "").trim() && !isKwikEmbedUrl(s.iframe_url)
    );
    if (embed) return embed;
    const kwik = servers.find((s) => isKwikEmbedUrl(s?.iframe_url || ""));
    if (kwik) return kwik;
    return servers[0];
  }

  function renderServers(servers) {
    const box = $("#movie-servers");
    if (!box) return;
    box.innerHTML = (servers || [])
      .map(
        (s, i) =>
          `<button type="button" class="btn ghost sm movies-server-btn${i === 0 ? " active" : ""}" data-iframe="${escapeHtml(s.iframe_url || "")}" data-mp4="${escapeHtml(s.mp4 || "")}" data-m3u8="${escapeHtml(s.m3u8 || "")}" data-referer="${escapeHtml(s.referer || s.iframe_url || s.mp4 || currentMovieUrl || "")}" data-quality="${escapeHtml(s.quality || "")}">${escapeHtml(serverButtonLabel(s) || `Server ${i + 1}`)}</button>`
      )
      .join("");

    box.querySelectorAll(".movies-server-btn").forEach((btn) => {
      btn.onclick = () => {
        box.querySelectorAll(".movies-server-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        playServer(btn.dataset.iframe || btn.dataset.mp4, {
          m3u8: btn.dataset.m3u8 || "",
          mp4: btn.dataset.mp4 || "",
          referer: btn.dataset.referer || "",
        });
      };
    });

    const first = pickAutoPlayServer(servers);
    if (first?.iframe_url || first?.m3u8 || first?.mp4) {
      playServer(first.iframe_url || first.mp4 || "", {
        m3u8: first.m3u8 || "",
        mp4: first.mp4 || "",
        referer: first.referer || first.iframe_url || first.mp4 || currentMovieUrl || "",
      });
    }
  }

  async function startHlsPlayback(video, m3u8, referer, pageUrl) {
    stopTsSegmentPlayer();
    show(video);
    const embedUrl = canEmbedPageFallback() ? pageUrl : "";
    const proxied = hlsProxyUrl(m3u8, referer);

    if (shouldPreferNativeHls()) {
      playNativeHls(video, proxied, embedUrl);
      return;
    }

    if (isDesktopLike()) {
      const started = attachHlsJs(video, proxied, embedUrl, referer, m3u8);
      if (!started) {
        if (canEmbedPageFallback() && pageUrl) {
          const saved = loadProgress(currentMovieUrl);
          showEmbed(pageUrl, saved);
        } else {
          throw new Error(codeCatalogPlaybackHint());
        }
        return;
      }
      if (canEmbedPageFallback() && pageUrl) {
        window.setTimeout(() => {
          if (hlsInstance && video.paused && !video.ended && pageUrl) {
            destroyPlayer();
            const saved = loadProgress(currentMovieUrl);
            void showEmbed(pageUrl, saved);
          }
        }, 12000);
      }
      return;
    }

    if (attachHlsJs(video, proxied, embedUrl, referer, m3u8)) {
      return;
    }

    if (canPlayNativeHls()) {
      playNativeHls(video, proxied, embedUrl);
    } else if (canEmbedPageFallback() && pageUrl) {
      const saved = loadProgress(currentMovieUrl);
      showEmbed(pageUrl, saved);
    } else {
      throw new Error(
        canEmbedPageFallback()
          ? "Browser tidak mendukung HLS — gunakan Chrome/Firefox terbaru."
          : codeCatalogPlaybackHint()
      );
    }
  }

  async function playServer(iframeUrl, preset = {}) {
    const presetMp4 = (preset.mp4 || "").trim();
    const pageUrl = iframeUrl || presetMp4 || currentMovieUrl || "";
    if (!pageUrl && !preset.m3u8 && !presetMp4) return;
    destroyPlayer();
    setPlayerStatus("Menyiapkan stream…");
    const video = $("#movie-player");
    rememberStream(null, pageUrl);

    if (presetMp4 && !preset.m3u8 && !iframeUrl) {
      rememberStream(
        {
          ok: true,
          player_mode: "mp4",
          mp4: presetMp4,
          referer: preset.referer || currentMovieUrl || "",
          source: currentMovieSource,
          save_supported: true,
          save_provider: "mp4",
        },
        presetMp4
      );
      playDirectMp4(video, presetMp4, preset.referer || currentMovieUrl || "", pageUrl);
      return;
    }

    if (pageUrl && isP2pEmbedUrl(pageUrl) && canEmbedPageFallback() && !preset.m3u8) {
      rememberStream(
        {
          ok: true,
          player_mode: "embed",
          embed_url: pageUrl,
          iframe: pageUrl,
          referer: pageUrl,
          source: "p2p_embed",
        },
        pageUrl
      );
      const saved = loadProgress(currentMovieUrl);
      showEmbed(pageUrl, saved);
      return;
    }

    try {
      let m3u8 = (preset.m3u8 || "").trim();
      let referer = (preset.referer || pageUrl).trim();
      let streamPayload = null;

      if (!m3u8 && pageUrl) {
        const q = new URLSearchParams({ url: pageUrl });
        streamPayload = await api(`${movieApiBase(currentMovieSource)}/stream?${q}`);
        rememberStream(streamPayload, pageUrl);
        m3u8 = streamPayload.m3u8 || "";
        referer = streamPayload.referer || streamPayload.iframe || pageUrl;
      } else if (m3u8) {
        rememberStream(
          {
            m3u8,
            referer,
            iframe: pageUrl,
            embed_url: pageUrl,
            player_mode: "hls",
          },
          pageUrl
        );
      }

      const mp4 = (streamPayload?.mp4 || "").trim();
      const kwikExternal =
        streamPayload?.player_mode === "kwik_external" || isKwikEmbedUrl(pageUrl);
      const embedOnly =
        !kwikExternal &&
        (streamPayload?.player_mode === "embed" ||
          (!m3u8 &&
            !mp4 &&
            canEmbedPageFallback() &&
            !/workers\.dev|\.mp4(\?|$)/i.test(pageUrl) &&
            !isKwikEmbedUrl(pageUrl)));

      if (kwikExternal && !mp4 && !m3u8) {
        const kwikUrl = resolvedEmbedUrl(streamPayload, pageUrl) || pageUrl;
        rememberStream(
          {
            ...(streamPayload || {}),
            player_mode: "kwik_external",
            embed_url: kwikUrl,
            iframe: kwikUrl,
          },
          pageUrl
        );
        showKwikExternal(kwikUrl);
        return;
      }

      if (mp4 && !m3u8) {
        rememberStream(streamPayload, pageUrl);
        const qualities = Array.isArray(streamPayload?.qualities) ? streamPayload.qualities : [];
        updateQualityPicker(qualities, streamPayload?.quality || "");
        playDirectMp4(
          video,
          mp4,
          referer,
          pageUrl,
          streamPayload?.mp4_play_url || ""
        );
        return;
      }

      if (embedOnly && canEmbedPageFallback()) {
        const embedSrc = resolvedEmbedUrl(streamPayload, pageUrl);
        if (!embedSrc) {
          throw new Error("Link player tidak ditemukan — coba server/episode lain.");
        }
        const saved = loadProgress(currentMovieUrl);
        rememberStream(
          {
            ...(streamPayload || {}),
            embed_url: embedSrc,
            iframe: embedSrc,
            player_mode: "embed",
          },
          pageUrl
        );
        showEmbed(embedSrc, saved);
        return;
      }

      if (!m3u8) {
        throw new Error(
          currentMovieSource === "code_catalog"
            ? codeCatalogPlaybackHint()
            : "Link stream tidak tersedia"
        );
      }

      await startHlsPlayback(video, m3u8, referer, pageUrl);
    } catch (e) {
      currentStream = null;
      setMovieSaveButtonEnabled(false);
      setPlayerStatus(e.message || "Gagal memuat stream.");
    }
  }

  function getMovieSaveMode() {
    return $("#movie-save-mode")?.value || "telegram";
  }

  function updateMovieSaveModalUi() {
    const mode = getMovieSaveMode();
    const folderSection = $("#movie-save-folder-section");
    const startBtn = $("#btn-movie-save-start");
    const needsFolder = mode === "telegram" || mode === "both";
    if (folderSection) {
      folderSection.style.display = needsFolder ? "" : "none";
    }
    if (startBtn) {
      startBtn.textContent =
        mode === "download"
          ? "Unduh ke perangkat"
          : mode === "both"
            ? "Simpan + unduh"
            : "Simpan ke folder";
      if (!needsFolder) {
        startBtn.disabled = false;
      } else {
        startBtn.disabled = movieSaveFolderId == null;
      }
    }
  }

  async function loadMovieSaveQualities() {
    const sel = $("#movie-save-quality");
    const hint = $("#movie-save-quality-hint");
    if (!sel) return;
    sel.innerHTML = '<option value="">Otomatis (terbaik)</option>';
    if (isOtakudesuSource() && episodeDownloads.length) {
      const saveable = episodeDownloads.filter((d) => d.save_supported);
      const seen = new Set();
      for (const item of saveable) {
        const q = (item.quality || "").trim();
        if (!q || seen.has(q)) continue;
        seen.add(q);
        const opt = document.createElement("option");
        opt.value = q;
        opt.textContent = q;
        sel.appendChild(opt);
      }
      if (hint) {
        hint.textContent = saveable.length
          ? "Pilih resolusi unduhan MP4."
          : "Unduhan otomatis tidak tersedia untuk episode ini.";
      }
      sel.onchange = () => {
        const q = sel.value || "";
        const item = episodeDownloads.find((d) => d.save_supported && d.quality === q);
        if (item) rememberDownload(item);
      };
      if (currentDownload?.quality) {
        sel.value = currentDownload.quality;
      }
      return;
    }
    if (
      !currentStream ||
      (!isHydrxSaveable(currentStream, currentStream.iframe_url) &&
        !isBloggerSaveable(currentStream, currentStream.iframe_url))
    ) {
      if (hint) {
        hint.textContent = "Resolusi detail tersedia untuk server HYDRX.";
      }
      return;
    }
    if (isBloggerSaveable(currentStream, currentStream.iframe_url)) {
      if (hint) {
        hint.textContent = "Blogger — satu kualitas MP4 (otomatis).";
      }
      return;
    }
    if (hint) hint.textContent = "Memuat resolusi HYDRX…";
    try {
      const q = new URLSearchParams({
        iframe_url: currentStream.iframe_url || "",
        referer: currentStream.referer || "",
        movie_url: currentStream.movie_url || currentMovie?.url || "",
        m3u8: currentStream.m3u8 || "",
      });
      const data = await api(`/api/movies/qualities?${q}`);
      const items = data.qualities || [];
      for (const item of items) {
        if ((item.label || "").toLowerCase() === "auto") continue;
        const opt = document.createElement("option");
        opt.value = item.label || String(item.res_id || "");
        const sizeLabel = item.size_mb ? `~${item.size_mb} MB` : "";
        opt.textContent = sizeLabel
          ? `${item.label} (${sizeLabel})`
          : String(item.label || "auto");
        sel.appendChild(opt);
      }
      if (hint) {
        hint.textContent =
          items.length > 1
            ? "Pilih resolusi HYDRX — ukuran lebih kecil = unduh lebih cepat."
            : "Hanya satu kualitas tersedia untuk stream ini.";
      }
    } catch (e) {
      if (hint) {
        hint.textContent = e.message || "Gagal memuat resolusi — pakai otomatis.";
      }
    }
  }

  function renderMovieSaveFolders(folders) {
    const list = $("#movie-save-folder-list");
    const loading = $("#movie-save-folder-loading");
    const startBtn = $("#btn-movie-save-start");
    if (!list) return;
    hide(loading);
    const defaultId =
      typeof window.tdGetCurrentFolderId === "function"
        ? window.tdGetCurrentFolderId()
        : 0;
    const norm = (id) =>
      typeof window.tdNormalizeFolderId === "function"
        ? window.tdNormalizeFolderId(id)
        : Number(id) || 0;

    if (!folders.length) {
      list.innerHTML = `<p class="hint">Belum ada folder. Buat folder di tab Drive dulu.</p>`;
      movieSaveFolderId = null;
      if (startBtn) startBtn.disabled = true;
      return;
    }

    let pick = folders.find((f) => norm(f.id) === norm(defaultId));
    if (!pick) pick = folders[0];
    movieSaveFolderId = norm(pick.id);
    movieSaveFolderName = pick.name || "Folder";

    list.innerHTML = folders
      .map((f) => {
        const fid = norm(f.id);
        const active = fid === movieSaveFolderId;
        const icon = f.is_saved ? "★" : "📁";
        return `<button type="button" class="movie-save-folder-item${active ? " active" : ""}" data-folder-id="${fid}" role="option" aria-selected="${active}">
          <span class="folder-icon">${icon}</span>
          <span>${escapeHtml(f.name || "Folder")}</span>
        </button>`;
      })
      .join("");

    list.querySelectorAll(".movie-save-folder-item").forEach((btn) => {
      btn.onclick = () => {
        movieSaveFolderId = norm(btn.dataset.folderId);
        const f = folders.find((x) => norm(x.id) === movieSaveFolderId);
        movieSaveFolderName = f?.name || "Folder";
        list.querySelectorAll(".movie-save-folder-item").forEach((el) => {
          const on = norm(el.dataset.folderId) === movieSaveFolderId;
          el.classList.toggle("active", on);
          el.setAttribute("aria-selected", on ? "true" : "false");
        });
        if (startBtn) startBtn.disabled = false;
      };
    });
    updateMovieSaveModalUi();
  }

  async function openMovieSaveModal() {
    if (
      !currentStream?.download_url &&
      !currentStream?.iframe_url &&
      !currentStream?.m3u8
    ) {
      notifyError(
        isOtakudesuSource()
          ? "Unduhan belum siap — tunggu episode selesai dimuat atau ganti episode."
          : "Putar film dulu (pilih server) sebelum menyimpan."
      );
      return;
    }
    const errBox = $("#movie-save-error");
    hide(errBox);
    errBox.textContent = "";
    const titleHint = $("#movie-save-film-title");
    if (titleHint) {
      let t = currentStream.title || currentMovie?.title || "Film";
      titleHint.textContent = t;
    }
    const list = $("#movie-save-folder-list");
    const loading = $("#movie-save-folder-loading");
    if (list) list.innerHTML = "";
    show(loading);
    const startBtn = $("#btn-movie-save-start");
    if (startBtn) startBtn.disabled = true;

    updateMovieSaveModalUi();
    void loadMovieSaveQualities();

    if (typeof window.tdOpenModal === "function") {
      window.tdOpenModal("modal-movie-save");
    }

    try {
      const mode = getMovieSaveMode();
      if (mode === "download") {
        hide(loading);
        updateMovieSaveModalUi();
        return;
      }
      const { folders } = await api("/api/folders");
      renderMovieSaveFolders(folders || []);
    } catch (e) {
      hide(loading);
      if (list) {
        list.innerHTML = "";
      }
      if (errBox) {
        errBox.textContent = e.message || "Gagal memuat folder";
        show(errBox);
      }
    }
  }

  async function startMovieSaveToTelegram() {
    const errBox = $("#movie-save-error");
    hide(errBox);
    if (errBox) errBox.textContent = "";

    const mode = getMovieSaveMode();
    const quality = $("#movie-save-quality")?.value || "";
    const needsFolder = mode === "telegram" || mode === "both";
    if (needsFolder && movieSaveFolderId == null) {
      notifyError("Pilih folder tujuan Telegram.");
      return;
    }
    if (!currentStream) {
      notifyError("Stream belum siap — putar film dulu.");
      return;
    }

    const folderName = movieSaveFolderName || "folder";
    const saveKind =
      currentStream.save_provider === "otakudesu_download"
        ? `OtakuDesu MP4${currentStream.quality ? ` (${currentStream.quality})` : ""}`
        : currentStream.save_provider === "hydrx" || isHydrxSaveable(currentStream)
          ? "HYDRX (Tambuk)"
          : currentStream.save_provider === "blogger" || isBloggerSaveable(currentStream)
            ? "Blogger (Samehadaku)"
            : currentStream.m3u8
              ? "HLS"
              : "stream";
    const qualityNote = quality ? ` Resolusi: ${quality}.` : "";
    const modeText =
      mode === "download"
        ? "mengunduh ke perangkat Anda"
        : mode === "both"
          ? `mengunggah ke Telegram ("${folderName}") sekaligus menyiapkan unduhan perangkat`
          : `mengunggah ke folder Telegram "${folderName}"`;
    const confirmFn = window.tdShowConfirm;
    const ok = confirmFn
      ? await confirmFn({
          title: "Mulai unduh film?",
          message: `Server akan mengunduh dari ${saveKind} lalu ${modeText}.${qualityNote} Proses background — pantau di menu Downloads.`,
          okLabel: "Ya, mulai",
        })
      : true;
    if (!ok) return;

    const body = {
      folder_id: needsFolder ? movieSaveFolderId : 0,
      title: currentStream.title || currentMovie?.title || "film",
      m3u8: currentStream.m3u8 || "",
      referer: currentStream.referer || "",
      iframe_url: currentStream.iframe_url || "",
      movie_url: currentStream.movie_url || currentMovie?.url || "",
      download_url: currentStream.download_url || "",
      mode,
      quality,
    };

    const startBtn = $("#btn-movie-save-start");
    if (startBtn) startBtn.disabled = true;

    const closeSaveModals = () => {
      if (typeof window.tdHideTransferLoader === "function") {
        window.tdHideTransferLoader();
      }
      if (typeof window.tdCloseModal === "function") {
        window.tdCloseModal("modal-transfer");
        window.tdCloseModal("modal-movie-save");
      }
    };

    try {
      const r = await api("/api/movies/lk21/save-to-telegram", {
        method: "POST",
        body,
      });
      closeSaveModals();
      notifySuccess(
        r.message ||
          `"${body.title}" masuk antrian unduhan. Pantau progress di menu Downloads.`
      );
      if (typeof window.tdShowAppPanel === "function") {
        window.tdShowAppPanel("downloads");
      }
      if (typeof loadMovieDownloads === "function") {
        loadMovieDownloads();
      }
    } catch (e) {
      closeSaveModals();
      const msg = e.message || "Gagal menyimpan film";
      if (errBox) {
        errBox.textContent = msg;
        show(errBox);
      }
      notifyError(msg);
    } finally {
      if (startBtn) startBtn.disabled = movieSaveFolderId == null;
    }
  }

  function applyMovieDetailToUi(data) {
    const titleEl = $("#movie-detail-title");
    const subEl = $("#movie-detail-sub");
    const posterEl = $("#movie-detail-poster");
    const posterWrap = posterEl ? posterEl.parentElement : null;
    const synEl = $("#movie-detail-synopsis");
    if (titleEl) titleEl.textContent = data.title || "—";
    if (subEl) {
      const epCount = Array.isArray(data.episodes) ? data.episodes.length : data.episode_count || 0;
      subEl.textContent = [
        data.year,
        data.rating ? `★ ${data.rating}` : null,
        epCount ? `${epCount} episode` : null,
        data.runtime,
        data.type,
      ]
        .filter(Boolean)
        .join(" · ");
    }
    if (posterEl && posterWrap) {
      const poster =
        data.poster ||
        (currentMovie && normalizeMovieUrl(currentMovie.url) === currentMovieUrl
          ? currentMovie.poster
          : "");
      if (poster) {
        applyPosterImg(posterEl, poster, data.title || "", currentMovieSource);
      } else {
        posterEl.removeAttribute("src");
        posterEl.style.display = "none";
      }
    }
    if (synEl) {
      const syn = data.synopsis || "";
      if (syn) {
        synEl.textContent = syn;
        show(synEl);
      } else {
        hide(synEl);
      }
    }
    if (Array.isArray(data.episodes) && data.episodes.length) {
      renderEpisodes(data.episodes, currentEpisodeIndex);
    } else {
      currentEpisodeIndex = 0;
      renderEpisodes([]);
      renderServers(data.servers || []);
    }
    const saved = loadProgress(currentMovieUrl);
    if (saved > 30) {
      setPlayerStatus(
        `Ada progress ${formatTime(saved)} — akan lanjut otomatis saat player siap.`
      );
    } else {
      setPlayerStatus("Pilih server jika belum otomatis diputar.");
    }
  }

  async function openMovieDetail(url, source = "lk21") {
    if (!url) return;
    const reqId = ++movieDetailGen;
    if (source === "code_catalog") {
      currentMovieSource = "code_catalog";
    } else if (source === "otakudesu" || /otakudesu/i.test(url)) {
      currentMovieSource = "otakudesu";
    } else if (source === "nontonanimeid" || source === "samehadaku" || /nontonanimeid|samehadaku/i.test(url)) {
      currentMovieSource = "nontonanimeid";
    } else if (source === "tambuk" || /tambuk\.sbs/i.test(url)) {
      currentMovieSource = "tambuk";
    } else {
      currentMovieSource = "lk21";
    }
    currentEpisodeIndex = 0;
    currentMovieUrl = normalizeMovieUrl(url);
    recentPlaybackMarked = null;
    updateResumeBar(currentMovieUrl);
    if (typeof window.syncAppState === "function") {
      window.syncAppState();
    }
    showDetailView();
    destroyPlayer();

    const titleEl = $("#movie-detail-title");
    const posterEl = $("#movie-detail-poster");
    const posterWrap = posterEl ? posterEl.parentElement : null;
    const synEl = $("#movie-detail-synopsis");
    if (posterWrap) show(posterWrap);

    const cached =
      currentMovie &&
      normalizeMovieUrl(currentMovie.url || url) === currentMovieUrl
        ? currentMovie
        : null;
    if (cached) {
      applyMovieDetailToUi(cached);
    } else {
      if (titleEl) titleEl.textContent = "Memuat…";
      $("#movie-servers").innerHTML = "";
      setPlayerStatus("Memuat detail…");
    }

    try {
      const q = new URLSearchParams({ url });
      const data = await api(`${movieApiBase(currentMovieSource)}/detail?${q}`);
      if (reqId !== movieDetailGen) return;
      if ($("#panel-movies")?.classList.contains("hidden")) return;
      currentMovie = data;
      if (data.url) currentMovieUrl = normalizeMovieUrl(data.url);
      updateResumeBar(currentMovieUrl);
      applyMovieDetailToUi(data);
      if (typeof window.syncAppState === "function") {
        window.syncAppState();
      }
    } catch (e) {
      if (reqId !== movieDetailGen) return;
      if (titleEl) titleEl.textContent = "Gagal memuat";
      setPlayerStatus(e.message || "Detail film gagal dimuat.");
      notifyError(e.message);
    }
  }

  function onShow(initial = null) {
    if (typeof window.updateTopbarForPanel === "function") window.updateTopbarForPanel();
    void refreshCodeCatalogStatus();
    if (initial) {
      setState(initial);
    }
    const inp = $("#movie-search-input");
    if (inp) inp.value = searchQuery || "";
    syncTabs();

    const movieToOpen = movieUrlFromLocation();
    if (movieToOpen) {
      openMovieDetail(movieToOpen);
    } else {
      currentMovieUrl = null;
      showBrowse();
      if (lastList?.movies?.length) {
        renderMovies(lastList);
      } else {
        hide($("#movies-loading"));
        hide($("#movies-error"));
      }
      void loadList();
    }
    renderRecentMovies();
  }

  function bind() {
    window.addEventListener("message", onEmbedWindowMessage);
    document.querySelectorAll("[data-movie-kind]").forEach((btn) => {
      btn.addEventListener("click", () => {
        kind = btn.dataset.movieKind || "new";
        searchQuery = "";
        page = 1;
        syncTabs();
        loadList();
        if (typeof window.syncAppState === "function") window.syncAppState();
      });
    });

    $("#form-movie-search")?.addEventListener("submit", (e) => {
      e.preventDefault();
      searchQuery = ($("#movie-search-input")?.value || "").trim();
      page = 1;
      syncTabs();
      loadList();
      if (typeof window.syncAppState === "function") window.syncAppState();
    });

    $("#btn-movies-back")?.addEventListener("click", showBrowse);
    $("#btn-movies-back-detail")?.addEventListener("click", showBrowse);

    $("#btn-movie-save-telegram")?.addEventListener("click", () => openMovieSaveModal());
    $("#btn-movie-save-start")?.addEventListener("click", () => startMovieSaveToTelegram());
    $("#movie-save-mode")?.addEventListener("change", async () => {
      updateMovieSaveModalUi();
      const mode = getMovieSaveMode();
      if (mode === "download") return;
      const list = $("#movie-save-folder-list");
      if (list?.querySelector(".movie-save-folder-item")) return;
      const loading = $("#movie-save-folder-loading");
      const errBox = $("#movie-save-error");
      show(loading);
      try {
        const { folders } = await api("/api/folders");
        renderMovieSaveFolders(folders || []);
      } catch (e) {
        hide(loading);
        if (errBox) {
          errBox.textContent = e.message || "Gagal memuat folder";
          show(errBox);
        }
      }
    });

    $("#btn-movie-resume")?.addEventListener("click", () => seekPlaybackToSaved());

    $("#btn-movies-recent-clear")?.addEventListener("click", async () => {
      const ok =
        typeof window.tdShowConfirm === "function"
          ? await window.tdShowConfirm({
              title: "Hapus riwayat tontonan?",
              message: "Daftar terakhir ditonton di browser ini akan dikosongkan.",
              okLabel: "Hapus",
              danger: true,
            })
          : confirm("Hapus riwayat tontonan?");
      if (ok) clearRecentHistory();
    });
  }

  bind();

  return {
    onShow,
    onHide,
    showBrowse,
    getState,
    setState,
    refreshCodeCatalogStatus: refreshCodeCatalogStatus,
    renderPagination: renderMoviesPagination,
  };
})();

window.MoviesPanel = MoviesPanel;