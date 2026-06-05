# Menambah tema dari design.md

1. Salin blok `[data-theme="default"]` di `themes.css`, ganti id (mis. `retro`).
2. Isi variabel CSS sesuai design.md (bg, surface, accent, dll.).
3. Jika butuh gaya khusus (font, tekstur, shadow), buat `theme-{id}.css` dan link di `index.html`.
4. Tambah entri di `THEME_OPTIONS` (`app.js`) dan `.theme-swatch-{id}` di `style.css`.
5. Hard refresh browser setelah deploy.

Tema tersimpan di `localStorage` key `td-theme`.

**Retro** — grunge 90s: `theme-retro.css`.

**Glass** — glassmorphism minimal: latar abu gelap + `backdrop-filter`, `theme-glass.css`.