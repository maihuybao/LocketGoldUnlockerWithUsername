# Deploy

Hướng dẫn chạy & deploy Locket Gold Username. Có 3 mode:

- **Local dev** — chạy nhanh trên máy bạn
- **Local production** — test gunicorn trước khi deploy
- **VPS** — Ubuntu/Debian + gunicorn + systemd + nginx (Cloudflare proxy ở edge)

> File hỗ trợ trong `deploy/`: `locket.service` (systemd), `nginx-cloudflare.conf` (Cloudflare-fronted), `nginx.conf` (Let's Encrypt direct, không Cloudflare), `DEPLOY.md` (chi tiết VPS có hardening).

---

## Yêu cầu

- Python 3.10+
- SQLite 3.35+ (đã có sẵn trên Ubuntu 22.04+ / macOS / hầu hết distro hiện đại)
- VPS: Ubuntu 22.04+ hoặc Debian 12+, ≥ 512MB RAM, domain trỏ A-record về IP

## Biến môi trường

Tạo `.env` từ `.env.example`. Tất cả là string; trường để trống = không bật.

| Var                                          | Mô tả                                   | Ghi chú                                           |
| -------------------------------------------- | ----------------------------------------- | -------------------------------------------------- |
| `ADMIN_USERNAME`                           | Username đăng nhập `/admin`          | Bắt buộc                                         |
| `ADMIN_PASSWORD`                           | Password đăng nhập `/admin`          | Bắt buộc                                         |
| `FLASK_SECRET_KEY`                         | Khoá session cookie                      | Set để admin không bị logout sau restart       |
| `BEHIND_HTTPS`                             | `1` khi có nginx + TLS phía trước   | Bật cookie `Secure` + tin `X-Forwarded-Proto` |
| `EMAIL`, `PASSWORD`                      | Seed account đầu tiên khi DB rỗng     | Sau đó quản lý qua `/admin`                  |
| `gist_token_url`                           | Fallback URL JSON cho RevenueCat payloads | Khi `tokens` table rỗng                         |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Notification khi restore success          | Optional                                           |
| `LOCKET_DB`                                | Đường dẫn SQLite                      | Default `locket.db`                              |

App **boot được với 0 account**: `/api/restore` trả 503, admin login OK để bạn add account đầu tiên qua UI.

---

## 1. Local dev

```bash
pip install -r requirements.txt
cp .env.example .env
# Mở .env, set ADMIN_USERNAME + ADMIN_PASSWORD tối thiểu

python wsgi.py
```

Server: `http://localhost:5001`

- `/` — trang user
- `/admin/login` — đăng nhập
- `/admin/` — dashboard

## 2. Local production (test gunicorn)

Trước khi deploy VPS, chạy thử với cấu hình production để bắt sớm vấn đề:

```bash
gunicorn -c gunicorn.conf.py wsgi:app
```

Output ra stdout, port 5001 same. `Ctrl+C` để dừng.

Lưu ý: gunicorn config khoá `workers=1, preload_app=False`. Đừng tăng workers — QueueManager spawn N daemon thread per worker process, nhiều process = N×workers thread cùng poll DB, lãng phí.

Free port nếu kẹt:

```bash
lsof -ti:5001 | xargs kill -9
```

---

## 3. Deploy VPS (Ubuntu / Debian)

### 3.1 Cài gói

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git nginx
```

> Bỏ qua `certbot` nếu dùng Cloudflare proxy — Cloudflare tự terminate TLS ở edge, origin chỉ chạy HTTP.

### 3.2 Tạo user `locket`

```bash
sudo adduser --system --group --shell /bin/bash --home /home/locket locket
```

### 3.3 Clone + venv

```bash
sudo -u locket -i
cd ~
git clone https://github.com/YOU/LocketGoldUsername.git
cd LocketGoldUsername
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
exit
```

### 3.4 Tạo `.env`

```bash
sudo -u locket nano /home/locket/LocketGoldUsername/.env
```

Tối thiểu:

```env
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<random_password>
FLASK_SECRET_KEY=<openssl rand -hex 32>
BEHIND_HTTPS=1

# Optional
EMAIL=
PASSWORD=
gist_token_url=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

> Sinh secret: `openssl rand -base64 24` (password), `openssl rand -hex 32` (secret key). Chạy trên máy bạn rồi paste — `nano` không evaluate `$(...)`.

Khoá file:

```bash
sudo chmod 600 /home/locket/LocketGoldUsername/.env
sudo chown locket:locket /home/locket/LocketGoldUsername/.env
```

### 3.5 systemd service

```bash
sudo cp /home/locket/LocketGoldUnlockerWithUsername/deploy/locket.service /etc/systemd/system/locket.service
sudo systemctl daemon-reload
sudo systemctl enable --now locket
sudo systemctl status locket
```

Mong đợi `Active: active (running)`. Logs:

```bash
sudo journalctl -u locket -f
```

Đầu ra mẫu:

```
db: initialized at locket.db
AccountRotator: 0 accounts configured. App will start but ...
Queue manager initialized with 0 worker(s)
[INFO] Listening at: http://127.0.0.1:5001
```

Test loopback:

```bash
curl -I http://127.0.0.1:5001/
```

### 3.6 nginx (Cloudflare-fronted)

Dùng config `nginx-cloudflare.conf` — listen HTTP-only trên port 80, đọc real client IP từ header `CF-Connecting-IP`, tin `X-Forwarded-Proto` để biết user đến qua HTTPS.

```bash
sudo cp /home/locket/LocketGoldUsername/deploy/nginx-cloudflare.conf /etc/nginx/sites-available/locket
sudo sed -i 's/YOUR_DOMAIN/locketgold.me/g' /etc/nginx/sites-available/locket
sudo ln -sf /etc/nginx/sites-available/locket /etc/nginx/sites-enabled/locket
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

### 3.7 Cấu hình Cloudflare

Trong dashboard Cloudflare cho domain:

1. **DNS** → A record `locket` (hoặc `@`) trỏ về IP VPS, **proxy status = orange cloud (Proxied)**.
2. **SSL/TLS → Overview** → chọn mode:
   - **Flexible** — đơn giản nhất, CF↔origin là HTTP. OK cho nội bộ, không khuyến nghị production vì traffic CF↔VPS là plaintext.
   - **Full** (recommended) — CF↔origin qua HTTPS, accept self-signed cert. Cần thêm 1 server block 443 với Origin Certificate (xem dưới).
   - **Full (strict)** — như Full nhưng cert phải valid. Dùng Origin Certificate của Cloudflare (free, valid 15 năm).
3. **SSL/TLS → Edge Certificates** → bật **Always Use HTTPS** để CF tự redirect HTTP user → HTTPS.

App hoạt động ngay với Flexible. Để chuyển sang Full(strict):

```bash
# Lấy Origin Certificate từ Cloudflare → SSL/TLS → Origin Server → Create
sudo mkdir -p /etc/ssl/cloudflare
sudo nano /etc/ssl/cloudflare/origin.pem    # paste cert
sudo nano /etc/ssl/cloudflare/origin.key    # paste key
sudo chmod 600 /etc/ssl/cloudflare/origin.key
```

Thêm block 443 vào `/etc/nginx/sites-available/locket`:
```nginx
server {
    listen 443 ssl http2;
    server_name locketgold.me;
    ssl_certificate     /etc/ssl/cloudflare/origin.pem;
    ssl_certificate_key /etc/ssl/cloudflare/origin.key;
    # paste lại nguyên `location /` block từ phần HTTP
}
```

Reload nginx + đổi Cloudflare SSL/TLS sang **Full (strict)**.

> Sau khi xác nhận CF hoạt động, uncomment `if ($http_cf_connecting_ip = "")` trong nginx config để chặn truy cập trực tiếp IP VPS — buộc traffic phải qua Cloudflare.

### 3.8 Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

Port 5001 KHÔNG mở ra ngoài — gunicorn listen 127.0.0.1.

### 3.9 Login + cấu hình lần đầu

Mở `https://locketgold.me/admin/login`. Đăng nhập bằng `ADMIN_USERNAME` / `ADMIN_PASSWORD`.

Trong dashboard:

- **Tài khoản** — Add Locket account (Test login trước khi save)
- **Tokens** — Paste payload RevenueCat (override gist)
- **Site settings** — Theme/layout, popup banner, maintenance mode (chặn user, bypass admin)
- **Queue** — Realtime view jobs đang chạy
- **History** — 30 entries gần nhất

---

## Vận hành

| Việc            | Lệnh                                                                                                                                                        |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Logs live        | `sudo journalctl -u locket -f`                                                                                                                             |
| Restart app      | `sudo systemctl restart locket`                                                                                                                            |
| Reload nginx     | `sudo systemctl reload nginx`                                                                                                                              |
| Pull update      | `sudo -u locket -i` → `cd LocketGoldUsername && git pull && .venv/bin/pip install -r requirements.txt` → `exit` → `sudo systemctl restart locket` |
| Backup DB        | `sudo -u locket sqlite3 /home/locket/LocketGoldUsername/locket.db ".backup /home/locket/backups/locket-$(date +%F).db"`                                    |
| Bật maintenance | Admin → Site settings → toggle Maintenance                                                                                                                 |

### Cron backup

`sudo crontab -e`:

```cron
0 3 * * * sudo -u locket sqlite3 /home/locket/LocketGoldUsername/locket.db ".backup /home/locket/backups/locket-$(date +\%F).db"
```

`.backup` của SQLite dùng online backup API — an toàn dù app đang ghi.

---

## Troubleshooting

| Triệu chứng                                           | Nguyên nhân thường gặp                                                                                               |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `journalctl` báo `ADMIN_PASSWORD not set` ở login | `.env` thiếu var hoặc `EnvironmentFile=` trong service trỏ sai path                                                |
| Login redirect loop                                     | `BEHIND_HTTPS=1` nhưng truy cập qua HTTP, hoặc nginx không pass `X-Forwarded-Proto`                               |
| 502 Bad Gateway                                         | gunicorn chết hoặc chưa khởi động —`sudo systemctl status locket`                                                |
| Restore luôn 503                                       | Chưa có account hoặc tất cả slot bị remove                                                                          |
| `restorePurchase` fail                                | `tokens` table rỗng + `gist_token_url` không reachable. Add payload qua admin.                                      |
| Worker không xử lý job                               | Account creds sai → 401 liên tục → log `refresh failed` lặp lại. Test login lại trong admin.                     |
| Đổi domain                                            | Sửa `server_name` trong nginx config + reload nginx + cập nhật DNS Cloudflare. App không cần restart. |

---

## Cảnh báo bảo mật

- `locket.db` chứa **password Locket plaintext** + payload RevenueCat. Quyền file phải 600, owner `locket:locket`. Backup cũng phải chmod 600.
- Đừng commit `.env` hay `locket.db` (đã có trong `.gitignore`).
- `ADMIN_PASSWORD` nên ≥ 16 ký tự random — admin panel có quyền add/remove account, edit maintenance, đổi tokens.
- Cân nhắc thêm IP allowlist cho `/admin*` trong nginx (uncomment block trong `deploy/nginx.conf`) nếu chỉ admin từ IP cố định cần truy cập.
