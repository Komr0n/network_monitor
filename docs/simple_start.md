direct-IP режим без nginx: сервис должен слушать 0.0.0.0:8000, а шаблон .env надо брать из deploy/linux/.env.linux.ip.example.

На Ubuntu выполни это по порядку:

cd /opt/network_monitor

sudo apt update
sudo apt install -y python3 python3-venv python3-pip ca-certificates

sudo chown -R $USER:$USER /opt/network_monitor

python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

cp deploy/linux/.env.linux.ip.example .env

python3 - <<'PY'
from pathlib import Path
import secrets

path = Path(".env")
text = path.read_text(encoding="utf-8")
text = text.replace("JWT_SECRET=replace-with-a-long-random-secret", f"JWT_SECRET={secrets.token_urlsafe(48)}")
text = text.replace("DEFAULT_ADMIN_PASSWORD=ChangeMeNow123!", f"DEFAULT_ADMIN_PASSWORD={secrets.token_urlsafe(12)}")
path.write_text(text, encoding="utf-8", newline="\n")
print("Updated .env with random JWT_SECRET and admin password")
PY

grep -E "APP_HOST|APP_PORT|TRUSTED_HOSTS|SESSION_HTTPS_ONLY|FORCE_HTTPS|DEFAULT_ADMIN_PASSWORD" .env

chmod +x deploy/linux/quickstart.sh
./deploy/linux/quickstart.sh
Что должно быть в .env:

APP_HOST=0.0.0.0
APP_PORT=8000
TRUSTED_HOSTS=*
SESSION_HTTPS_ONLY=false
FORCE_HTTPS=false
После запуска проверь на Ubuntu:

curl http://127.0.0.1:8000/health
curl http://$(hostname -I | awk '{print $1}'):8000/health
ss -ltnp | grep 8000
Если с Ubuntu работает, а с Windows всё ещё нет, значит уже не код, а сеть/VirtualBox. Тогда проверь:

В VirtualBox у сетевого адаптера должен быть Bridged Adapter или Host-Only Adapter.
Если стоит NAT, добавь port forwarding Host 8000 -> Guest 8000 или переключи адаптер.
На Ubuntu открой порт:
sudo ufw allow 8000/tcp
sudo ufw status
Если хочешь, можно сразу сделать постоянный запуск как сервис без nginx:

cd /opt/network_monitor
sudo useradd --system --create-home --home-dir /opt/network_monitor --shell /usr/sbin/nologin network-monitor || true
sudo chown -R network-monitor:network-monitor /opt/network_monitor
sudo chmod +x /opt/network_monitor/deploy/linux/run-network-monitor.sh
sudo cp /opt/network_monitor/deploy/linux/network-monitor.service /etc/systemd/system/network-monitor.service
sudo systemctl daemon-reload
sudo systemctl enable network-monitor
sudo systemctl restart network-monitor
sudo systemctl status network-monitor
И потом открывай с Windows:

http://IP_ТВОЕЙ_UBUNTU:8000
Смысл проблемы был такой:

раньше Windows-запрос доходил до FastAPI, но резался по TRUSTED_HOSTS;
если после правки будет timeout, виноват уже VirtualBox/firewall.