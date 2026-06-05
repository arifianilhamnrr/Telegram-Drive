# Tema Telegram Drive Web

Tema diatur lewat variabel CSS di `frontend/static/themes.css`.

## Tema bawaan

| ID | Nama |
|----|------|
| `default` | Telegram Drive (gelap biru) |
| `ocean` | Ocean Teal |
| `dusk` | Dusk Purple |
| `light` | Light Clean |

## Menambah tema dari design

1. Kirim file referensi design (`.md` / mockup / palet warna).
2. Salin blok `[data-theme="default"]` di `themes.css`.
3. Ganti ID menjadi `data-theme="nama-tema-baru"`.
4. Daftarkan di `THEME_OPTIONS` array di `app.js`.
5. (Opsional) Tambah preview swatch di modal Pengaturan.

Pengguna memilih tema di **Pengaturan** — disimpan di `localStorage` key `td-theme`.