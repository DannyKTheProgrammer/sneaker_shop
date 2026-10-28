# Sneaker Shop API — учебный бэкенд магазина кроссовок

FastAPI + PostgreSQL 17 (в Docker Compose) + синхронный SQLAlchemy 2 + Alembic +
Pydantic 2; пароли — passlib[bcrypt], токены — python-jose (JWT HS256).

Что умеет: публичный каталог; регистрация, вход и `GET /auth/me`; запись
каталога только администратором; личная корзина; оформление заказа одной
транзакцией (снимок цен, списание остатка, очистка корзины); история своих
заказов; смена статуса заказа администратором (`created → processing →
completed`).

## Требования

| Что | Нужно | Реально проверено |
|---|---|---|
| Python | 3.11+ | **3.12.3** (Windows 11, PowerShell) |
| Docker | Docker с Compose v2 (`docker compose …`) | Docker 26.0.0, Compose v2.26.1 (Docker Desktop) |
| Свободные порты | `127.0.0.1:55433` (PostgreSQL), `8000` (API) | — |

Python 3.11 и 3.13, macOS и Linux **не проверялись**. Команды для bash/zsh
приведены по стандартному синтаксису; на Windows в Git Bash проверялась только
команда создания `.env`.

## Где выполнять команды

Все команды — из **корня проекта**: папки, где лежат `compose.yaml`,
`requirements.txt` и `alembic.ini`.

## Быстрый старт

### 1. Новое виртуальное окружение и зависимости

venv — отдельная папка с Python и пакетами только этого проекта.

bash / zsh:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip check
```

PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip check
```

`requirements.txt` — полный закреплённый стек (точные версии всех пакетов).
`pip check` должен ответить `No broken requirements found.`
Если PowerShell запрещает `Activate.ps1`, можно не активировать окружение и
вызывать `.\.venv\Scripts\python.exe` вместо `python`.

### 2. Файл настроек `.env` — только если его ещё нет

`.env` хранит локальные настройки и в Git не попадает. Существующий `.env`
не перезаписывайте: в нём уже может быть ваш ключ подписи.

bash / zsh:

```bash
[ -f .env ] || cp .env.example .env
```

PowerShell:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Значения в `.env.example` — вымышленные учебные (`practice` /
`practice-only`). Для чего-то кроме практики их нужно заменить.

### 3. Ключ подписи JWT

```bash
python setup_jwt.py
```

Скрипт кладёт случайный ключ (64 символа) в `.env`, значение не печатает и
уже существующий ключ не трогает. Запасного ключа в коде нет: без
`JWT_SECRET` (или если он короче 32 символов) приложение не стартует.

### 4. База данных

```bash
docker compose up -d --wait
```

`--wait` ждёт, пока healthcheck контейнера (`pg_isready`) станет зелёным.

**Адреса:** PostgreSQL внутри контейнера слушает порт **5432**, а наружу
опубликован только на `127.0.0.1:`**55433**. Приложение запускается на вашем
компьютере, поэтому в `DATABASE_URL` стоит `127.0.0.1:55433`. Адрес
`db:5432` работает только между контейнерами в сети Compose.

### 5. Схема и стартовый каталог

```bash
python -m alembic upgrade head
python -m app.seed
```

Миграция создаёт семь таблиц: `users`, `brands`, `products`, `product_sizes`,
`cart_items`, `orders`, `order_items`. Seed добавляет 2 бренда, 3 товара и
4 размера с фиксированными id (101, 102, 201, 301). Seed можно запускать
повторно: он вставляет только отсутствующие строки (`ON CONFLICT DO
NOTHING`) и **не сбрасывает** уже изменённые цены и остатки.

### 6. Запуск API

```bash
python -m uvicorn app.final_main:app --port 8000
```

- Swagger UI: <http://127.0.0.1:8000/docs>
- Проверка БД: <http://127.0.0.1:8000/health> → `{"status":"ok"}`;
  если БД недоступна → `503 {"status":"unavailable"}`.

`/docs` открывается и без БД: доступность БД подтверждает только `/health`.

## Администратор

Публичного маршрута «сделать админом» нет. Сначала обычная регистрация,
затем роль выдаёт консольная команда (прямой `UPDATE` в БД из `DATABASE_URL`).
Пароль придумайте сами (не короче 8 символов) и никуда не коммитьте.

bash / zsh (второй терминал, API запущен):

```bash
curl -s -X POST http://127.0.0.1:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@example.com", "password": "<ваш-пароль>"}'

python -m app.promote_admin --email admin@example.com
```

PowerShell:

```powershell
$body = @{ email = "admin@example.com"; password = "<ваш-пароль>" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/auth/register `
  -ContentType "application/json" -Body $body

python -m app.promote_admin --email admin@example.com
```

Проверка роли: `POST /auth/login` → скопируйте `access_token` → кнопка
**Authorize** в `/docs` → `GET /auth/me` должен показать `"is_admin": true`.
Роль читается из БД на каждом запросе, поэтому уже выданный токен получает
права сразу. Токен живёт 15 минут; не публикуйте его.

## Карта API

| Маршрут | Доступ |
|---|---|
| `GET /health` | всем |
| `GET /brands`, `GET /products`, `GET /products/{id}` | всем |
| `POST /auth/register`, `POST /auth/login` | всем |
| `GET /auth/me` | с токеном |
| `POST /products`, `PUT /products/{id}`, `DELETE /products/{id}` | администратор |
| `POST /cart/items`, `GET /cart`, `PATCH /cart/items/{item_id}`, `DELETE /cart/items/{item_id}` | с токеном, только своя корзина |
| `POST /orders`, `GET /orders`, `GET /orders/{order_id}` | с токеном, только свои заказы |
| `PATCH /orders/{order_id}/status` | администратор |

Коды ошибок: 401 — нет входа или токен не принят; 403 — нет роли; 404 —
объекта нет или он чужой (тела одинаковы); 409 — состояние не позволяет
(нет остатка, пустая корзина, недопустимый переход статуса,
`concurrent_change_retry`); 422 — неверная форма запроса (значения из
запроса, включая пароль, в ответ не возвращаются).

**Важное ограничение:** полной идемпотентности нет. Если ответ на
`POST /orders` потерялся после фиксации заказа, не повторяйте его вслепую —
сначала проверьте `GET /orders`.

## Проверка после запуска

1. `GET /health` → 200 `{"status":"ok"}`.
2. `GET /products` → три товара из seed.
3. Без токена `GET /auth/me` → 401; с токеном → ваш пользователь.
4. Под обычным пользователем: `POST /cart/items {"product_size_id": 101,
   "quantity": 2}` и `{"product_size_id": 201, "quantity": 1}` →
   `POST /orders` → 201, `"total": "21970.00"`.
5. Под обычным пользователем `PATCH /orders/{id}/status` → 403; под
   администратором `{"status": "processing"}` → 200.

## Остановка

```bash
# остановить API: Ctrl+C в его терминале
docker compose stop      # остановить контейнер БД, данные сохраняются
docker compose down      # удалить контейнер, именованный том pgdata остаётся
```

`docker compose down -v` **удаляет том с данными** — используйте только если
действительно хотите начать с пустой БД (потом снова шаги 4–5).

## Модули

| Путь | Назначение |
|---|---|
| [`app/final_main.py`](app/final_main.py) | финальная точка входа (полный состав M3–M5) |
| [`app/app_factory.py`](app/app_factory.py) | сборка приложения, `/health`, обработчики ошибок |
| [`app/core/config.py`](app/core/config.py) | настройки из окружения и `.env` (`DATABASE_URL`, `JWT_SECRET` обязательны) |
| [`app/core/security.py`](app/core/security.py) | bcrypt-хэши паролей, выдача и проверка JWT |
| [`app/core/errors.py`](app/core/errors.py) | бизнес-ошибки → безопасный `{"detail": "..."}` |
| [`app/database.py`](app/database.py) | Engine, фабрика сессий, `get_session` |
| [`app/models/entities.py`](app/models/entities.py) | ORM-модели семи таблиц |
| [`app/schemas/`](app/schemas/) | Pydantic-схемы запросов и ответов |
| [`app/routers/`](app/routers/) | HTTP-маршруты; зависимости входа и роли — в [`app/routers/auth.py`](app/routers/auth.py) |
| [`app/services/shop.py`](app/services/shop.py) | корзина и заказы, менеджер транзакции `transaction()` |
| [`app/services/catalog.py`](app/services/catalog.py), [`app/services/auth.py`](app/services/auth.py) | каталог и пользователи |
| [`app/seed.py`](app/seed.py) | идемпотентный seed каталога |
| [`app/promote_admin.py`](app/promote_admin.py) | выдача роли администратора |
| [`alembic/versions/201c8afcccba_initial.py`](alembic/versions/201c8afcccba_initial.py) | единственная миграция (семь таблиц) |
| [`compose.yaml`](compose.yaml) | PostgreSQL 17, порт `127.0.0.1:55433`, том `pgdata` |
| [`setup_jwt.py`](setup_jwt.py) | генерация локального `JWT_SECRET` |

Сохранённые учебные точки входа: `app/catalog_main.py` (M2: только чтение
каталога), `app/store_main.py` и `app/main.py` (та же полная сборка, что и
`final_main`; оставлены для прежних команд).

## Типовые ошибки запуска

| Симптом | Причина и что сделать |
|---|---|
| `docker compose` → `error during connect … docker daemon is not running` / `open //./pipe/docker_engine` | Docker Desktop не запущен — запустить и повторить шаг 4 |
| `POSTGRES_USER is required (см. .env.example)` | нет `.env` рядом с `compose.yaml` — шаг 2 |
| `Bind for 127.0.0.1:55433 failed: port is already allocated` | порт занят другим контейнером/PostgreSQL — освободить или согласованно поменять порт в `compose.yaml` и `DATABASE_URL` |
| `ValidationError … database_url Field required` / `jwt_secret Field required` | нет `.env` или в нём нет строки — шаги 2–3 |
| `JWT_SECRET must be at least 32 characters` | ключ пустой (скопирован из `.env.example`) — `python setup_jwt.py` |
| `Create .env from .env.example first` (от `setup_jwt.py`) | сначала шаг 2 |
| `ModuleNotFoundError: No module named 'fastapi'` (или `jose`, `pydantic_settings`) | команда запущена не тем Python — активировать `.venv` или вызвать `.venv\Scripts\python.exe` |
| `connection refused` / `connection timeout expired` от Alembic или seed | БД не запущена или ещё не готова — `docker compose ps`, затем шаг 4 |
| `/health` → 503 | API не достучался до БД — те же причины, детали в консоли uvicorn |
| `[Errno 10048]` / `address already in use` на 8000 | порт API занят — другой `--port`, например 8001 |
| Кракозябры вместо русского текста в консоли Windows | запустить с `PYTHONUTF8=1` (PowerShell: `$env:PYTHONUTF8 = "1"`) или `chcp 65001` |
| 401 на `/auth/me` с ранее рабочим токеном | токен истёк (15 минут) — снова `POST /auth/login` |
| 403 на записи каталога после регистрации админа | не выполнен `python -m app.promote_admin --email …` |
| `alembic upgrade head` ничего не делает | схема уже актуальна (`alembic current` → `201c8afcccba (head)`) — это нормально |
