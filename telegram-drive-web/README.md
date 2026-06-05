# Telegram Drive Web

Aplikasi web untuk mengelola file di Telegram lewat browser — upload, unduh, pratinjau, dan organisasi folder (Saved Messages & channel privat) dengan antarmuka mirip drive cloud.

**Stack:** Python 3.11+, [FastAPI](https://fastapi.tiangolo.com/), [Telethon](https://docs.telethon.dev/), frontend HTML/CSS/JS statis.

---

## Fitur

### Akun & keamanan
- Registrasi / login akun aplikasi (username + password, disimpan lokal di server)
- Opsional **gate password** (`WEB_ACCESS_PASSWORD`) — lapisan sandi sebelum halaman login
- Admin pertama dari `ADMIN_USERNAME` / `ADMIN_PASSWORD` saat database masih kosong
- Ubah password, logout akun

### Telegram
- Login Telegram via **API ID / API Hash** ([my.telegram.org](https://my.telegram.org/apps))
- OTP telepon, dukungan 2FA
- Folder: Saved Messages + channel privat yang Anda buat
- Upload file tunggal / bulk, hapus, unduh (termasuk ZIP bulk)
- Import dari **URL langsung** atau **Google Drive** (link publik)

### Daftar file
- Filter: Semua, Foto, Video, Dokumen
- Pencarian nama file + pagination (24 file/halaman)
- Kartu file responsif (mobile & desktop)

### Pratinjau media
- Gambar & video inline (termasuk **MOV** di mobile dengan HTTP Range + MIME `video/quicktime`)
- **PDF** di modal (PDF.js, scroll semua halaman, fit lebar)
- Audio streaming

### UI / UX
- Beberapa **tema** (default, ocean, dusk, light, retro, glass) — disimpan di `localStorage`
- Sidebar mobile dengan animasi & backdrop
- Modal konfirmasi untuk aksi berbahaya

### Admin (opsional)
- Upload / tes cookies **yt-dlp** untuk YouTube (jika fitur import YouTube diaktifkan di deployment Anda)

> Catatan: UI import YouTube/yt-dlp dapat disembunyikan di frontend; backend tetap mendukung jika cookies dikonfigurasi.

---

## Persyaratan

| Komponen | Versi / catatan |
|----------|-----------------|
| Python | 3.10+ (disarankan 3.11) |
| ffmpeg | Opsional, untuk yt-dlp / konversi media |
| Reverse proxy | Disarankan di production (nginx, Caddy) |

---

## Instalasi lokal (development)

Cocok untuk laptop/PC — tanpa Docker.

```bash
git clone https://github.com/arifianilhamnrr/Telegram-Drive.git
cd Telegram-Drive/telegram-drive-web   # atau folder root jika repo hanya berisi web

cp .env.example .env
# Edit .env: SECRET_KEY, ADMIN_USERNAME, ADMIN_PASSWORD (opsional)

chmod +x run.sh
./run.sh
```

Buka **http://127.0.0.1:8080** (port default lokal di `run.sh`).

**Langkah pertama di browser:**
1. Daftar / login akun aplikasi  
2. **Pengaturan → Telegram** → masukkan API ID & Hash → verifikasi OTP  
3. Upload atau import file ke folder yang dipilih  

**Data lokal:** `data/sessions/` (session Telethon), `data/users.db` (akun aplikasi).

### Manual (tanpa `run.sh`)

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
mkdir -p data/sessions

export DATA_DIR=./data
uvicorn backend.main:app --host 127.0.0.1 --port 8080 --reload
```

---

## Instalasi production (VPS)

Deploy **native** dengan systemd (disarankan — tanpa Docker harian).

```bash
git clone https://github.com/arifianilhamnrr/Telegram-Drive.git
cd Telegram-Drive/telegram-drive-web

cp .env.example .env
nano .env   # WAJIB: SECRET_KEY, ADMIN_USERNAME, ADMIN_PASSWORD, APP_PORT

chmod +x install-vps.sh update.sh restart.sh
sudo ./install-vps.sh
```

Script akan:
- Membuat virtualenv & menginstal dependensi
- Men-generate `SECRET_KEY` jika kosong
- Mendaftarkan service **`telegram-drive-web`** (systemd, auto-start)
- Menjalankan app di `127.0.0.1:APP_PORT` (default **14202**)

**Cek service:**

```bash
systemctl status telegram-drive-web
curl -s http://127.0.0.1:14202/health
journalctl -u telegram-drive-web -n 50 --no-pager
```

### Reverse proxy (nginx / aaPanel)

Proxy HTTPS ke proses lokal, contoh:

```nginx
location / {
    proxy_pass http://127.0.0.1:14202;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 2048m;   # sesuaikan MAX_UPLOAD_MB
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
}
```

Untuk upload besar, samakan `client_max_body_size` dengan `MAX_UPLOAD_MB` di `.env`.

### Update setelah `git pull`

```bash
cd telegram-drive-web
bash update.sh
```

`update.sh` menginstal ulang dependensi, me-restart systemd, dan mengecek `/health`.

### Pindah VPS

1. Salin folder proyek + isi `data/` (session & `users.db`)  
2. Jalankan `./install-vps.sh` di server baru  
3. Pasang kembali DNS + reverse proxy  

---

## Docker (opsional)

```bash
cp .env.example .env
# Set APP_PORT di .env jika perlu

docker compose up -d --build
# Dengan Caddy + HTTPS:
docker compose --profile proxy up -d --build
```

Atau: `bash deploy.sh` (wrapper deploy Docker).

Volume data: `telegram_drive_data` → `/app/data` di container.

---

## Variabel lingkungan (`.env`)

| Variabel | Default | Keterangan |
|----------|---------|------------|
| `SECRET_KEY` | — | **Wajib production** — cookie & signing |
| `WEB_ACCESS_PASSWORD` | kosong | Gate password opsional |
| `ALLOW_REGISTRATION` | `true` | Izinkan daftar akun baru |
| `ADMIN_USERNAME` | — | Username admin (bootstrap) |
| `ADMIN_PASSWORD` | — | Password admin awal |
| `MAX_UPLOAD_MB` | `2000` | Batas ukuran per file |
| `APP_PORT` | `8080` lokal / `14202` VPS | Port uvicorn |
| `HOST` | `127.0.0.1` | Bind address |
| `DATA_DIR` | `./data` | Session Telegram & database |
| `YT_DLP_COOKIES_FILE` | `data/ytdlp/cookies.txt` | Cookies YouTube (opsional) |

Salin dari `.env.example` lalu sesuaikan.

---

## Struktur proyek

```
telegram-drive-web/
├── backend/
│   ├── main.py              # API FastAPI
│   ├── telegram_mgr.py      # Telethon
│   ├── media_stream.py      # Preview/download + Range
│   ├── donation_settings.py
│   ├── url_fetcher.py       # Import URL / GDrive
│   └── user_store.py        # Akun aplikasi (SQLite)
├── frontend/static/         # UI (index.html, app.js, style.css)
├── data/                    # Runtime (gitignore, kecuali .gitkeep)
├── scripts/create_admin.py
├── run.sh                   # Lokal
├── install-vps.sh           # Production sekali
├── update.sh                # Update + restart
└── requirements.txt
```

---

## Perintah berguna

| Perintah | Kegunaan |
|----------|----------|
| `./run.sh` | Jalankan lokal |
| `./install-vps.sh` | Install systemd di VPS |
| `./update.sh` | Update kode + restart service |
| `./restart.sh` | Restart cepat |
| `python scripts/create_admin.py` | Buat admin jika DB kosong |

---

## API singkat

| Endpoint | Deskripsi |
|----------|-----------|
| `GET /health` | Status service |
| `GET /api/config` | Konfigurasi publik |

Dokumentasi OpenAPI dinonaktifkan di production (`docs_url=None`).

---

## Tema

Lihat [docs/THEMES.md](docs/THEMES.md) untuk menambah tema CSS.

---

## Dukungan pengembangan

Kalau aplikasi ini membantu Anda, dukungan lewat Saweria sangat dihargai:

**[saweria.co/arifianilhamnr](https://saweria.co/arifianilhamnr)**

---

## Lisensi

Proyek ini merupakan bagian / fork pengembangan **Telegram Drive**. Sesuaikan lisensi dengan repositori induk Anda.

---

## Kontribusi & masalah

Issue & PR: [github.com/arifianilhamnrr/Telegram-Drive](https://github.com/arifianilhamnrr/Telegram-Drive)