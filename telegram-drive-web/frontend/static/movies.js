/* LK21 Movie — scrape + HLS proxy backend */

const MoviesPanel = (() => {
  const $ = (sel) => document.querySelector(sel);

  let kind = "new";
  let page = 1;
  let searchQuery = "";
  let lastList = null;
  let hlsInstance = null;
  let currentMovie = null;
  let currentMovieUrl = null;
  /** @type {{ m3u8?: string, referer?: string, iframe_url?: string, title?: string } | null} */
  let currentStream = null;
  let movieSaveFolderId = null;
  let movieSaveFolderName = "";

  function getState() {
    return { kind, page, q: searchQuery, movieUrl: currentMovieUrl };
  }

  function setState(s = {}) {
    if (s.kind) kind = s.kind;
    if (typeof s.page === "number" && s.page > 0) page = s.page;
    if (s.q !== undefined) searchQuery = s.q || "";
    if (s.movieUrl) currentMovieUrl = s.movieUrl;
  }

  // Watch progress for resume (local only for now)
  function getProgressKey(url) {
    if (!url) return null;
    return `td-movie-progress:${url}`;
  }
  function saveProgress(url, seconds) {
    const key = getProgressKey(url);
    if (!key || !seconds || seconds < 30) return;
    try {
      localStorage.setItem(key, seconds.toString());
    } catch (e) {}
  }
  function loadProgress(url) {
    const key = getProgressKey(url);
    if (!key) return 0;
    try {
      const v = localStorage.getItem(key);
      return v ? parseFloat(v) : 0;
    } catch (e) { return 0; }
  }
  function clearProgress(url) {
    const key = getProgressKey(url);
    if (key) {
      try { localStorage.removeItem(key); } catch(e){}
    }
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

  function destroyPlayer() {
    const video = $("#movie-player");
    const embed = $("#movie-player-embed");
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
    currentStream = null;
    setMovieSaveButtonEnabled(false);
  }

  function setMovieSaveButtonEnabled(on) {
    const btn = $("#btn-movie-save-telegram");
    if (btn) btn.disabled = !on;
  }

  function rememberStream(data, iframeUrl) {
    const title = (currentMovie?.title || "film").trim() || "film";
    const embedOnly =
      data?.player_mode === "embed" ||
      (!data?.m3u8 && !!(data?.embed_url || data?.iframe || iframeUrl));
    if (data?.m3u8) {
      currentStream = {
        m3u8: data.m3u8,
        referer: data.referer || data.iframe || iframeUrl || "",
        iframe_url: iframeUrl || "",
        title,
        embedOnly: false,
        movie_url: currentMovie?.url || "",
      };
      setMovieSaveButtonEnabled(true);
      return;
    }
    if (iframeUrl) {
      currentStream = {
        m3u8: "",
        referer: "",
        iframe_url: iframeUrl,
        title,
        embedOnly,
        movie_url: currentMovie?.url || "",
      };
      setMovieSaveButtonEnabled(true);
      return;
    }
    currentStream = null;
    setMovieSaveButtonEnabled(false);
  }

  function hlsProxyUrl(m3u8, referer) {
    const q = new URLSearchParams({ u: m3u8, r: referer || "" });
    return `/api/movies/lk21/hls?${q}`;
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

  function playNativeHls(video, streamUrl, embedUrl) {
    video.removeAttribute("crossorigin");
    const onErr = () => {
      if (embedUrl) {
        destroyPlayer();
        showEmbed(embedUrl);
        return;
      }
      setPlayerStatus("Gagal memutar (native HLS).");
    };
    video.addEventListener("error", onErr, { once: true });
    video.src = streamUrl;
    attachVideoResume(video, currentMovieUrl);
    video.addEventListener(
      "loadedmetadata",
      () => {
        video.play().catch(() => {});
        setPlayerStatus("Sedang diputar.");
      },
      { once: true }
    );
  }

  function attachHlsJs(video, streamUrl, embedUrl, referer, rawM3u8) {
    if (!window.Hls?.isSupported()) return false;

    let triedDirect = false;
    hlsInstance = new window.Hls({
      enableWorker: true,
      lowLatencyMode: false,
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
      if (embedUrl) {
        destroyPlayer();
        showEmbed(embedUrl);
        return;
      }
      setPlayerStatus("Gagal memutar — coba server TurboVIP.");
    });
  }

  function showEmbed(url) {
    const video = $("#movie-player");
    const embed = $("#movie-player-embed");
    if (!embed || !url) return;
    hide(video);
    embed.src = url;
    show(embed);
    setPlayerStatus("Memutar via player embed.");
  }

  function setPlayerStatus(text) {
    const el = $("#movie-player-status");
    if (el) el.textContent = text || "";
  }

  function attachVideoResume(video, movieUrl) {
    if (!video || !movieUrl) return;
    const saved = loadProgress(movieUrl);
    if (saved > 30) {
      const seekOnce = () => {
        if (video.duration && saved < video.duration - 30) {
          try { video.currentTime = saved; } catch(e){}
        }
        video.removeEventListener("loadedmetadata", seekOnce);
      };
      video.addEventListener("loadedmetadata", seekOnce, { once: true });
    }

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
  }

  function showBrowse() {
    hide($("#movies-detail"));
    show($("#movies-browse"));
    hide($("#btn-movies-back"));
    hide($("#btn-movies-back-detail"));
    destroyPlayer();
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
      let t = `${data.count || movies.length} film`;
      if (data.source && String(data.source).startsWith("scrape")) {
        t += " · scrape";
      }
      if (data.fallback && data.fallback_query) {
        t += ` (sumber alternatif: “${data.fallback_query}”)`;
      } else if (searchQuery) {
        t += ` · pencarian “${searchQuery}”`;
      }
      hint.textContent = t;
    }

    show(grid);
    grid.innerHTML = movies
      .map((m) => {
        const posterUrl = m.poster
          ? `/api/movies/lk21/poster?u=${encodeURIComponent(m.poster)}`
          : null;
        const poster = posterUrl
          ? `<img class="movie-card-poster" src="${escapeHtml(posterUrl)}" alt="" loading="lazy">`
          : `<div class="movie-card-poster"></div>`;
        const meta = [m.year, m.quality, m.rating ? `★ ${m.rating}` : null, m.duration]
          .filter(Boolean)
          .join(" · ");
        return `<button type="button" class="movie-card" data-movie-url="${escapeHtml(m.url)}">
          ${poster}
          <div class="movie-card-body">
            <p class="movie-card-title">${escapeHtml(m.title || "—")}</p>
            <p class="movie-card-meta">${escapeHtml(meta)}</p>
          </div>
        </button>`;
      })
      .join("");

    grid.querySelectorAll(".movie-card").forEach((card) => {
      card.onclick = () => openMovieDetail(card.dataset.movieUrl);
    });

    const totalPages = data.total_pages || 1;
    if (totalPages <= 1) {
      hide(pag);
      return;
    }
    show(pag);
    pag.innerHTML = `
      <button type="button" class="btn ghost sm" data-movie-page="prev" ${page <= 1 ? "disabled" : ""}>← Sebelumnya</button>
      <span class="hint">Halaman ${page} / ${totalPages}</span>
      <button type="button" class="btn ghost sm" data-movie-page="next" ${page >= totalPages ? "disabled" : ""}>Berikutnya →</button>`;
    pag.querySelector('[data-movie-page="prev"]')?.addEventListener("click", () => {
      if (page > 1) {
        page -= 1;
        loadList();
        if (typeof window.syncAppState === "function") window.syncAppState();
      }
    });
    pag.querySelector('[data-movie-page="next"]')?.addEventListener("click", () => {
      if (page < totalPages) {
        page += 1;
        loadList();
        if (typeof window.syncAppState === "function") window.syncAppState();
      }
    });
  }

  async function loadList() {
    const loading = $("#movies-loading");
    const err = $("#movies-error");
    const grid = $("#movies-grid");
    hide(err);
    hide(grid);
    hide($("#movies-pagination"));
    show(loading);
    if (loading) loading.textContent = "Memuat film…";

    try {
      let data;
      if (searchQuery) {
        const q = new URLSearchParams({ q: searchQuery, page: String(page) });
        data = await api(`/api/movies/lk21/search?${q}`);
      } else {
        const q = new URLSearchParams({ kind, page: String(page) });
        data = await api(`/api/movies/lk21/list?${q}`);
      }
      renderMovies(data);
      if (typeof window.syncAppState === "function") window.syncAppState();
    } catch (e) {
      hide(loading);
      show(err);
      if (err) err.textContent = e.message || "Gagal memuat daftar film.";
    }
  }

  function renderServers(servers) {
    const box = $("#movie-servers");
    if (!box) return;
    box.innerHTML = (servers || [])
      .map(
        (s, i) =>
          `<button type="button" class="btn ghost sm movies-server-btn${i === 0 ? " active" : ""}" data-iframe="${escapeHtml(s.iframe_url)}">${escapeHtml(s.label || s.provider || `Server ${i + 1}`)}</button>`
      )
      .join("");

    box.querySelectorAll(".movies-server-btn").forEach((btn) => {
      btn.onclick = () => {
        box.querySelectorAll(".movies-server-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        playServer(btn.dataset.iframe);
      };
    });

    const first = servers?.[0];
    if (first?.iframe_url) playServer(first.iframe_url);
  }

  async function playServer(iframeUrl) {
    if (!iframeUrl) return;
    destroyPlayer();
    setPlayerStatus("Menyiapkan stream…");
    const video = $("#movie-player");
    rememberStream(null, iframeUrl);
    try {
      const q = new URLSearchParams({ url: iframeUrl });
      const data = await api(`/api/movies/lk21/stream?${q}`);
      rememberStream(data, iframeUrl);
      const m3u8 = data.m3u8;
      const referer = data.referer || data.iframe || iframeUrl;
      const embedUrl = data.embed_url || data.iframe;
      const embedOnly =
        data.player_mode === "embed" || (!m3u8 && embedUrl);

      if (embedOnly && embedUrl) {
        showEmbed(embedUrl);
        setPlayerStatus("Memutar via player embed (mirror WP).");
        return;
      }

      if (!m3u8) {
        if (embedUrl) {
          showEmbed(embedUrl);
          return;
        }
        throw new Error("Link stream tidak tersedia");
      }

      const proxied = hlsProxyUrl(m3u8, referer);

      if (shouldPreferNativeHls()) {
        playNativeHls(video, proxied, embedUrl);
        return;
      }

      if (isDesktopLike() && embedUrl) {
        const started = attachHlsJs(video, proxied, embedUrl, referer, m3u8);
        if (!started) {
          showEmbed(embedUrl);
          return;
        }
        window.setTimeout(() => {
          if (hlsInstance && video.paused && !video.ended && embedUrl) {
            destroyPlayer();
            showEmbed(embedUrl);
          }
        }, 12000);
        return;
      }

      if (attachHlsJs(video, proxied, embedUrl, referer, m3u8)) {
        return;
      }

      if (canPlayNativeHls()) {
        playNativeHls(video, proxied, embedUrl);
      } else if (embedUrl) {
        showEmbed(embedUrl);
      } else {
        throw new Error("Browser tidak mendukung HLS — gunakan Chrome/Firefox terbaru.");
      }
    } catch (e) {
      currentStream = null;
      setMovieSaveButtonEnabled(false);
      setPlayerStatus(e.message || "Gagal memuat stream.");
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
    if (startBtn) startBtn.disabled = movieSaveFolderId == null;
  }

  async function openMovieSaveModal() {
    if (!currentStream?.iframe_url && !currentStream?.m3u8) {
      notifyError("Putar film dulu (pilih server) sebelum menyimpan.");
      return;
    }
    const errBox = $("#movie-save-error");
    hide(errBox);
    errBox.textContent = "";
    const titleHint = $("#movie-save-film-title");
    if (titleHint) {
      let t = currentStream.title || currentMovie?.title || "Film";
      if (currentStream.embedOnly && !currentStream.m3u8) {
        t += " — hanya embed P2P (simpan ke Telegram tidak tersedia)";
      }
      titleHint.textContent = t;
    }
    const list = $("#movie-save-folder-list");
    const loading = $("#movie-save-folder-loading");
    if (list) list.innerHTML = "";
    show(loading);
    const startBtn = $("#btn-movie-save-start");
    if (startBtn) startBtn.disabled = true;

    if (typeof window.tdOpenModal === "function") {
      window.tdOpenModal("modal-movie-save");
    }

    try {
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

    if (movieSaveFolderId == null) {
      notifyError("Pilih folder tujuan.");
      return;
    }
    if (!currentStream) {
      notifyError("Stream belum siap — putar film dulu.");
      return;
    }

    if (currentStream.embedOnly && !currentStream.m3u8) {
      const msg =
        "Mirror ini pakai player embed (P2P) — tidak bisa diunduh ke Telegram. " +
        "Ubah domain di Admin → LK21 ke tvN.lk21official.cc dan pilih server TurboVIP.";
      if (errBox) {
        errBox.textContent = msg;
        show(errBox);
      }
      notifyError(msg);
      return;
    }

    const folderName = movieSaveFolderName || "folder";
    const confirmFn = window.tdShowConfirm;
    const ok = confirmFn
      ? await confirmFn({
          title: "Simpan film ke Telegram?",
          message: `Server akan mengunduh film (ffmpeg) lalu mengunggah ke folder "${folderName}". Proses bisa lama untuk film panjang.`,
          okLabel: "Ya, simpan",
        })
      : true;
    if (!ok) return;

    const body = {
      folder_id: movieSaveFolderId,
      title: currentStream.title || currentMovie?.title || "film",
      m3u8: currentStream.m3u8 || "",
      referer: currentStream.referer || "",
      iframe_url: currentStream.iframe_url || "",
      movie_url: currentStream.movie_url || currentMovie?.url || "",
    };

    const startBtn = $("#btn-movie-save-start");
    if (startBtn) startBtn.disabled = true;

    try {
      const runSse = window.tdRunSsePost;
      if (!runSse) throw new Error("Fitur progress tidak tersedia — muat ulang halaman.");
      const r = await runSse("/api/movies/lk21/save-to-telegram", body, "Simpan film");
      if (typeof window.tdCloseModal === "function") {
        window.tdCloseModal("modal-movie-save");
      }
      notifySuccess(
        `${r.file?.name || body.title} (${formatSize(r.bytes || 0)}) disimpan ke "${folderName}".`
      );
    } catch (e) {
      if (typeof window.tdHideTransferLoader === "function") {
        window.tdHideTransferLoader();
      }
      const msg = e.message || "Gagal menyimpan film";
      if (errBox) {
        errBox.textContent = msg;
        show(errBox);
      }
      notifyError(msg);
    } finally {
      if (typeof window.tdHideTransferLoader === "function") {
        window.tdHideTransferLoader();
      }
      if (startBtn) startBtn.disabled = movieSaveFolderId == null;
    }
  }

  async function openMovieDetail(url) {
    if (!url) return;
    currentMovieUrl = url;
    if (typeof window.syncAppState === "function") {
      window.syncAppState();
    }
    showDetailView();
    destroyPlayer();
    setPlayerStatus("Memuat detail…");
    const titleEl = $("#movie-detail-title");
    const subEl = $("#movie-detail-sub");
    const posterEl = $("#movie-detail-poster");
    const posterWrap = posterEl ? posterEl.parentElement : null;
    const synEl = $("#movie-detail-synopsis");
    $("#movie-servers").innerHTML = "";

    // Always reserve/show the poster area so layout doesn't jump and bg is visible
    if (posterWrap) show(posterWrap);

    try {
      const q = new URLSearchParams({ url });
      const data = await api(`/api/movies/lk21/detail?${q}`);
      currentMovie = data;
      if (titleEl) titleEl.textContent = data.title || "—";
      if (subEl) {
        subEl.textContent = [
          data.year,
          data.rating ? `★ ${data.rating}` : null,
          data.runtime,
          data.type,
          data.scraped ? "via scrape" : null,
        ]
          .filter(Boolean)
          .join(" · ");
      }
      if (posterEl && posterWrap) {
        if (data.poster) {
          // Use our proxy so external lk21 poster images load reliably (no hotlink/CORS/referer blocks)
          const proxied = `/api/movies/lk21/poster?u=${encodeURIComponent(data.poster)}`;
          posterEl.src = proxied;
          posterEl.alt = data.title || "";
          posterEl.style.display = "";
          posterEl.onerror = () => {
            posterEl.style.display = "none";
          };
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
      renderServers(data.servers || []);
      setPlayerStatus("Pilih server jika belum otomatis diputar.");
    } catch (e) {
      if (titleEl) titleEl.textContent = "Gagal memuat";
      setPlayerStatus(e.message || "Detail film gagal dimuat.");
      notifyError(e.message);
    }
  }

  function onShow(initial = null) {
    if (initial) {
      setState(initial);
    }
    const inp = $("#movie-search-input");
    if (inp) inp.value = searchQuery || "";
    syncTabs();

    const movieToOpen = currentMovieUrl || (initial && initial.movieUrl);
    if (movieToOpen) {
      // deep link to specific movie detail
      openMovieDetail(movieToOpen);
    } else {
      // normal browse
      showBrowse();
      loadList();
    }
  }

  function bind() {
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
  }

  bind();

  return { onShow, showBrowse, getState, setState };
})();

window.MoviesPanel = MoviesPanel;