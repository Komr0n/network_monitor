Ниже готовый сценарий под Ubuntu Server 24.04 или Debian 12, от чистой машины до рабочего production.

Быстрый старт
Если хочешь просто поднять проект без nginx и без домена:

sudo apt update
sudo apt install -y python3 python3-venv python3-pip ca-certificates git
cd /opt
sudo mkdir -p /opt/network_monitor
sudo chown $USER:$USER /opt/network_monitor
cd /opt/network_monitor

# Скопируй сюда файлы проекта, затем:
chmod +x deploy/linux/quickstart.sh
./deploy/linux/quickstart.sh
После этого:

приложение поднимется на http://SERVER_IP:8000
.env создастся автоматически
пароль admin будет напечатан в консоли
Проверка:

curl http://127.0.0.1:8000/health
curl http://SERVER_IP:8000/health

Важно:

`quickstart.sh` поднимает приложение в режиме прямого доступа по IP/LAN:

- `APP_HOST=0.0.0.0`
- `TRUSTED_HOSTS=*`
- вход по `http://SERVER_IP:8000`

Если потом заменить `.env` на `deploy/linux/.env.linux.example`, приложение перейдёт в production-режим за `nginx`:

- `APP_HOST=127.0.0.1`
- прямой доступ к `http://SERVER_IP:8000` извне перестанет работать специально
- открывать нужно уже домен или `nginx`, а не порт `8000`

Если нужен постоянный запуск без домена и без `nginx`, используй шаблон `deploy/linux/.env.linux.ip.example`.

Полный Production
Если хочешь нормальный боевой запуск через systemd + nginx:

sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx curl git ca-certificates
sudo update-ca-certificates

sudo useradd --system --create-home --home-dir /opt/network_monitor --shell /usr/sbin/nologin network-monitor || true
sudo mkdir -p /opt/network_monitor
sudo chown -R network-monitor:network-monitor /opt/network_monitor
Скопируй проект в /opt/network_monitor, потом:

cd /opt/network_monitor
sudo -u network-monitor python3 -m venv .venv
sudo -u network-monitor ./.venv/bin/pip install --upgrade pip
sudo -u network-monitor ./.venv/bin/pip install -r requirements.txt
sudo chmod +x /opt/network_monitor/deploy/linux/run-network-monitor.sh
Создай .env:

# Для production за nginx:
sudo cp /opt/network_monitor/deploy/linux/.env.linux.example /opt/network_monitor/.env

# Для постоянного запуска напрямую по IP без nginx:
# sudo cp /opt/network_monitor/deploy/linux/.env.linux.ip.example /opt/network_monitor/.env

sudo chown network-monitor:network-monitor /opt/network_monitor/.env
sudo chmod 600 /opt/network_monitor/.env
sudo nano /opt/network_monitor/.env
Минимум что нужно поменять в .env:

JWT_SECRET=сюда_длинный_случайный_секрет
TRUSTED_HOSTS=your-domain.com,127.0.0.1,localhost
SESSION_HTTPS_ONLY=true
AUTO_CREATE_ADMIN=true
DEFAULT_ADMIN_PASSWORD=СложныйПароль123!
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
Поставь systemd service:

sudo cp /opt/network_monitor/deploy/linux/network-monitor.service /etc/systemd/system/network-monitor.service
sudo systemctl daemon-reload
sudo systemctl enable network-monitor
sudo systemctl start network-monitor
sudo systemctl status network-monitor
Проверка backend:

curl http://127.0.0.1:8000/health
Подключи nginx:

sudo cp /opt/network_monitor/deploy/linux/nginx-network-monitor.conf /etc/nginx/sites-available/network-monitor
sudo nano /etc/nginx/sites-available/network-monitor
Замени:

server_name monitor.example.com;
на свой домен.

Дальше:

sudo ln -sf /etc/nginx/sites-available/network-monitor /etc/nginx/sites-enabled/network-monitor
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx
sudo systemctl enable nginx
Если есть домен, включи HTTPS:

sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
Открой firewall:

sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
sudo ufw status
Что сделать после первого входа

Зайти под admin
Создать постоянного администратора
Сменить пароль
Потом в /opt/network_monitor/.env выключить bootstrap:
AUTO_CREATE_ADMIN=false
DEFAULT_ADMIN_PASSWORD=
И перезапустить:

sudo systemctl restart network-monitor
Где смотреть готовые файлы

Инструкция: linux-deploy.md
Быстрый запуск: quickstart.sh
Production runner: run-network-monitor.sh
Service: network-monitor.service
Nginx config: nginx-network-monitor.conf
Если хочешь, следующим сообщением я могу дать тебе уже совсем готовый набор команд именно под твой случай:

без домена, только по IP
или с доменом и HTTPS
и сразу с готовым .env шаблоном под Linux.



# Linux Deployment Guide

This guide prepares the project for a clean Linux deployment with:

1. `systemd` for process supervision.
2. `nginx` as the reverse proxy.
3. TLS termination on `nginx`.
4. A dedicated service user.
5. A production `.env` that works out of the box on Linux.

The recommended target is Ubuntu 24.04 LTS or Debian 12.

## Zero-Config First Start

If you want the shortest path from fresh server to working app, use this:

```bash
chmod +x deploy/linux/quickstart.sh
./deploy/linux/quickstart.sh
```

What it does automatically:

1. Creates `.venv` if it does not exist.
2. Installs Python dependencies.
3. Creates `.env` automatically if it is missing.
4. Generates a random `JWT_SECRET`.
5. Generates a random bootstrap admin password.
6. Starts the app on `0.0.0.0:8000`.
7. Sets `TRUSTED_HOSTS=*` so direct IP/LAN access works on first boot.

On first boot the console prints the generated admin password.

This path is intended for immediate launch on a Linux server without manual configuration.
It is a direct-access profile, not the hardened reverse-proxy production profile.

If you later replace the generated `.env` with `deploy/linux/.env.linux.example`, the app switches to localhost-only mode behind `nginx`:

1. `APP_HOST=127.0.0.1`
2. direct `http://SERVER_IP:8000` access stops working by design
3. clients must connect through `nginx` instead of port `8000`

After the first successful login, review `.env` and then continue with the production steps below if you want `systemd`, `nginx`, and HTTPS.

## Important Architecture Note

This application runs the monitoring scheduler inside the FastAPI process.

That means production must run **one application process only**.

Do not start multiple `uvicorn` workers and do not scale this app horizontally unless you first move the scheduler out of process.

## Deployment Layout

Recommended paths:

```text
/opt/network_monitor
/opt/network_monitor/.venv
/opt/network_monitor/.env
/opt/network_monitor/network_monitor.db
```

## 1. Install Base Packages

Run as `root` or with `sudo`:

```bash
apt update
apt install -y python3 python3-venv python3-pip nginx curl git ca-certificates
update-ca-certificates
```

If you plan to issue a public TLS certificate with Let's Encrypt:

```bash
apt install -y certbot python3-certbot-nginx
```

## 2. Create a Dedicated Service User

```bash
useradd --system --create-home --home-dir /opt/network_monitor --shell /usr/sbin/nologin network-monitor
mkdir -p /opt/network_monitor
chown -R network-monitor:network-monitor /opt/network_monitor
```

## 3. Copy the Project

Copy the repository contents into `/opt/network_monitor`.

Example:

```bash
rsync -av ./ /opt/network_monitor/
chown -R network-monitor:network-monitor /opt/network_monitor
```

## 4. Create the Virtual Environment

```bash
cd /opt/network_monitor
sudo -u network-monitor python3 -m venv .venv
sudo -u network-monitor ./.venv/bin/pip install --upgrade pip
sudo -u network-monitor ./.venv/bin/pip install -r requirements.txt
```

## 5. Create the Production `.env`

Choose the prepared Linux template that matches your topology:

```bash
# Reverse proxy production: nginx -> 127.0.0.1:8000
cp /opt/network_monitor/deploy/linux/.env.linux.example /opt/network_monitor/.env

# Direct IP / LAN deployment without nginx
# cp /opt/network_monitor/deploy/linux/.env.linux.ip.example /opt/network_monitor/.env

chown network-monitor:network-monitor /opt/network_monitor/.env
chmod 600 /opt/network_monitor/.env
```

Edit it:

```bash
nano /opt/network_monitor/.env
```

Use this baseline:

```env
DATABASE_URL=sqlite+aiosqlite:////opt/network_monitor/network_monitor.db

TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_CA_BUNDLE=

CHECK_INTERVAL=30
FAIL_THRESHOLD=3
TIMEOUT=2

JWT_SECRET=replace-with-a-long-random-secret
JWT_EXPIRATION=86400
SESSION_HTTPS_ONLY=true

APP_HOST=127.0.0.1
APP_PORT=8000
DEBUG=false
LOG_LEVEL=INFO

UVICORN_FORWARDED_ALLOW_IPS=127.0.0.1

TRUSTED_HOSTS=monitor.example.com,127.0.0.1,localhost
FORCE_HTTPS=false

AUTO_CREATE_ADMIN=true
DEFAULT_ADMIN_USERNAME=admin
DEFAULT_ADMIN_PASSWORD=ChangeMeNow123!
```

If you already started the app once through `python main.py` or `./deploy/linux/quickstart.sh`, a working `.env` may already exist.
In that case, either edit the generated file or replace it with the template that matches your deployment target.

Important:

1. `quickstart.sh` creates a direct-access profile for `http://SERVER_IP:8000`.
2. `deploy/linux/.env.linux.example` changes the app to `127.0.0.1:8000` for `nginx`.
3. `deploy/linux/.env.linux.ip.example` keeps direct IP/LAN access for a long-running service without `nginx`.

### Why these values are correct for Linux production

1. `APP_HOST=127.0.0.1`
   The app should listen only on localhost because `nginx` sits in front.

2. `APP_PORT=8000`
   This matches the provided `nginx` config template.

3. `SESSION_HTTPS_ONLY=true`
   Cookies should be secure in production.

4. `FORCE_HTTPS=false`
   HTTPS is terminated by `nginx`. App-level redirects are optional and not required for the provided setup.

5. `AUTO_CREATE_ADMIN=true`
   Use this only for first boot. After first login, disable it and clear `DEFAULT_ADMIN_PASSWORD`.

## 6. Prepare the Runner Script

Make the Linux launcher executable:

```bash
chmod +x /opt/network_monitor/deploy/linux/run-network-monitor.sh
```

This script starts the app with:

1. The project virtual environment Python.
2. The same `main.py` entrypoint used for local quick start.
3. A fail-fast check that requires an explicit production `.env` before the service starts.

It is the supported production entry point on Linux.

## 7. Smoke Test Without `systemd`

Before installing the service, verify the app manually:

```bash
cd /opt/network_monitor
sudo -u network-monitor ./deploy/linux/run-network-monitor.sh
```

In another shell:

```bash
curl http://127.0.0.1:8000/health
```

Expected result:

```json
{"status":"healthy", ...}
```

Stop the manual process with `Ctrl+C`.

## 8. Install the `systemd` Service

Copy the ready template:

```bash
cp /opt/network_monitor/deploy/linux/network-monitor.service /etc/systemd/system/network-monitor.service
```

Reload and enable:

```bash
systemctl daemon-reload
systemctl enable network-monitor
systemctl start network-monitor
```

Check status:

```bash
systemctl status network-monitor
journalctl -u network-monitor -n 200 --no-pager
```

## 9. Install and Enable `nginx`

Copy the provided site template:

```bash
cp /opt/network_monitor/deploy/linux/nginx-network-monitor.conf /etc/nginx/sites-available/network-monitor
```

Edit the server name:

```bash
nano /etc/nginx/sites-available/network-monitor
```

Replace:

```nginx
server_name monitor.example.com;
```

with your real domain.

Enable the site:

```bash
ln -sf /etc/nginx/sites-available/network-monitor /etc/nginx/sites-enabled/network-monitor
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl restart nginx
systemctl enable nginx
```

## 10. Enable HTTPS

If you have a real DNS name pointing to the server:

```bash
certbot --nginx -d monitor.example.com
```

Then update `.env` if needed:

```env
SESSION_HTTPS_ONLY=true
FORCE_HTTPS=false
TRUSTED_HOSTS=monitor.example.com,127.0.0.1,localhost
```

Restart the app:

```bash
systemctl restart network-monitor
```

## 11. Open the Firewall

If `ufw` is enabled:

```bash
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw enable
ufw status
```

Do not expose port `8000` publicly.

Only ports `80` and `443` should be reachable from the Internet.

## 12. Verify the Full Stack

Check the backend directly:

```bash
curl http://127.0.0.1:8000/health
```

Check the public endpoint:

```bash
curl -I http://monitor.example.com
curl -I https://monitor.example.com
curl https://monitor.example.com/health
```

If the site loads and `/health` responds, the deployment is operational.

## 13. First Login

Use:

1. `DEFAULT_ADMIN_USERNAME`
2. `DEFAULT_ADMIN_PASSWORD`

If `.env` was auto-generated, the first password is also printed to the console on first start.

Immediately after login:

1. Create a permanent admin user in the UI.
2. Change the password.
3. Edit `/opt/network_monitor/.env`:

```env
AUTO_CREATE_ADMIN=false
DEFAULT_ADMIN_PASSWORD=
```

Restart the service:

```bash
systemctl restart network-monitor
```

## 14. Telegram on Linux

On Linux, Telegram TLS usually works as long as the system CA package is installed:

```bash
apt install -y ca-certificates
update-ca-certificates
```

If your environment still uses a custom inspection CA:

1. Place the CA PEM file somewhere readable, for example:

```bash
/usr/local/share/ca-certificates/custom-inspection-ca.pem
```

2. Register it:

```bash
cp custom-inspection-ca.pem /usr/local/share/ca-certificates/custom-inspection-ca.crt
update-ca-certificates
```

3. Optionally point the app directly to it:

```env
TELEGRAM_CA_BUNDLE=/usr/local/share/ca-certificates/custom-inspection-ca.crt
```

Then restart:

```bash
systemctl restart network-monitor
```

## 15. Update Procedure

For updates:

```bash
cd /opt/network_monitor
sudo -u network-monitor git pull
sudo -u network-monitor ./.venv/bin/pip install -r requirements.txt
systemctl restart network-monitor
systemctl status network-monitor
```

## 16. Backup Procedure

The database is SQLite, so back up:

```bash
/opt/network_monitor/network_monitor.db
```

Simple backup example:

```bash
mkdir -p /opt/network_monitor/backups
cp /opt/network_monitor/network_monitor.db /opt/network_monitor/backups/network_monitor_$(date +%F_%H-%M-%S).db
```

## 17. Troubleshooting

### Service does not start

```bash
journalctl -u network-monitor -n 200 --no-pager
```

### `nginx` returns `502 Bad Gateway`

Check:

```bash
systemctl status network-monitor
curl http://127.0.0.1:8000/health
```

If `/health` fails locally, the app is not running correctly.

### `400 Invalid host header`

This means the request reached FastAPI, but the hostname or IP is not allowed by `TRUSTED_HOSTS`.

Typical cases:

1. You started with `quickstart.sh`, then later replaced `.env` with `deploy/linux/.env.linux.example`.
2. You are opening `http://SERVER_IP:8000` even though the app is now in reverse-proxy mode on `127.0.0.1:8000`.
3. The current server IP or DNS name is missing from `TRUSTED_HOSTS`.

Fixes:

1. For `nginx` mode, open the site through the configured domain or reverse proxy instead of port `8000`.
2. For direct IP/LAN mode, use `deploy/linux/.env.linux.ip.example`.
3. Add the final server IP or DNS name to `TRUSTED_HOSTS`, then restart `network-monitor`.

### Login works badly over HTTPS

Make sure:

```env
SESSION_HTTPS_ONLY=true
```

and the site is opened through `https://`.

### Telegram still fails on Linux

Check:

```bash
python3 - <<'PY'
import asyncio
from app.services.telegram_service import telegram_service

async def main():
    ok, msg = await telegram_service.send_test_message()
    print(ok)
    print(msg)

asyncio.run(main())
PY
```

If TLS fails on Linux even with `ca-certificates`, the server still has a custom CA or traffic inspection in the path.

## 18. Final Production Checklist

Before calling the deployment ready:

1. `JWT_SECRET` is unique and long.
2. `AUTO_CREATE_ADMIN=false` after first login.
3. `DEFAULT_ADMIN_PASSWORD` is empty after bootstrap.
4. Reverse-proxy mode uses `APP_HOST=127.0.0.1`.
5. Direct IP mode uses `APP_HOST=0.0.0.0`.
6. `APP_PORT=8000` matches your proxy or direct-access plan.
7. `SESSION_HTTPS_ONLY=true` when you serve the app through HTTPS.
8. `TRUSTED_HOSTS` contains the real domain or server IP, or is intentionally tightened after first boot.
9. `nginx` is serving HTTPS if you use reverse-proxy mode.
10. `/health` is green in the path users really use.
