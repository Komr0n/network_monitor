# Развертывание на Windows Server

## 1. Подготовка сервера

Установите:

1. Python 3.12+.
2. Git или способ копирования проекта на сервер.
3. `nssm` или любой другой менеджер Windows-служб.

Пример рабочей директории:

```powershell
C:\Apps\network_monitor
```

## 2. Копирование проекта

Скопируйте проект на сервер и создайте виртуальное окружение:

```powershell
cd C:\Apps\network_monitor
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
```

## 3. Настройка `.env`

Создайте `.env` на основе `.env.example` и задайте как минимум:

```env
JWT_SECRET=очень-длинный-случайный-ключ
APP_HOST=127.0.0.1
APP_PORT=1324
DEBUG=false
TRUSTED_HOSTS=monitor.company.local,127.0.0.1
SESSION_HTTPS_ONLY=true
FORCE_HTTPS=false
AUTO_CREATE_ADMIN=true
DEFAULT_ADMIN_USERNAME=admin
DEFAULT_ADMIN_PASSWORD=СложныйПароль
TELEGRAM_CA_BUNDLE=
```

После первого входа лучше:

1. Создать постоянного администратора через интерфейс.
2. Отключить `AUTO_CREATE_ADMIN`.
3. Очистить `DEFAULT_ADMIN_PASSWORD`.

## 4. Первый запуск вручную

Проверьте запуск приложения:

```powershell
cd C:\Apps\network_monitor
.\.venv\Scripts\Activate.ps1
python main.py
```

Проверьте:

1. Открывается `http://127.0.0.1:1324/health`.
2. Открывается веб-интерфейс.
3. Создаётся или используется файл `network_monitor.db`.

## 5. Запуск как служба Windows

Пример через `nssm`:

```powershell
nssm install NetworkMonitor
```

Заполните:

1. `Application`: `C:\Apps\network_monitor\.venv\Scripts\python.exe`
2. `Startup directory`: `C:\Apps\network_monitor`
3. `Arguments`: `main.py`

После этого:

```powershell
nssm start NetworkMonitor
```

Проверьте статус:

```powershell
Get-Service NetworkMonitor
```

## 6. Публикация для пользователей

Лучший вариант для реального сервера:

1. Держать приложение на `127.0.0.1:1324`.
2. Поставить перед ним IIS + ARR reverse proxy или Nginx for Windows.
3. На внешнем контуре завершать HTTPS в прокси.

Если работаете через IIS reverse proxy:

1. Публикуйте доменное имя, например `monitor.company.local`.
2. В `.env` задайте `TRUSTED_HOSTS=monitor.company.local,127.0.0.1`.
3. Включите `SESSION_HTTPS_ONLY=true`.
4. `FORCE_HTTPS` включайте только если прокси корректно отдает HTTPS-трафик и редиректы не зацикливаются.

Если временно открываете приложение напрямую по локальному IP без HTTPS, например `http://192.168.53.68:1234`:

1. Укажите `APP_HOST=0.0.0.0`.
2. Используйте правильный `APP_PORT`.
3. Добавьте IP в `TRUSTED_HOSTS`.
4. Временно поставьте `SESSION_HTTPS_ONLY=false`, иначе cookie-сессия не будет работать по обычному HTTP.
5. Проверьте, что порт открыт в Windows Firewall.

## 7. Обновление

При обновлении:

```powershell
cd C:\Apps\network_monitor
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
nssm restart NetworkMonitor
```

## 8. Что важно для продакшена

1. Не оставляйте `JWT_SECRET` значением по умолчанию.
2. Не храните боевые Telegram-токены в репозитории.
3. Держите резервную копию `network_monitor.db`.
4. Ограничьте доступ к порту приложения firewall'ом, если оно слушает не только `127.0.0.1`.
5. Регулярно проверяйте `/health` и логи службы.
6. При TLS-ошибках Telegram обновите корневые сертификаты Windows или задайте `TELEGRAM_CA_BUNDLE` с путём к PEM-файлу доверенного CA.
