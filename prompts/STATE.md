# STATE: учебный бэкенд магазина кроссовок

Язык общения с учеником: **только русский**. Английский — лишь по прямой
просьбе. Технические имена, команды и идентификаторы в коде остаются
латиницей, это не считается переходом на английский.

Файл для быстрого входа в контекст (в том числе в новой сессии агента).
Обновляется после каждого значимого шага. Последнее обновление: M3, часть 1
(контракт записи `ProductWrite`); HTTP-запись ждёт `require_admin`.

Реквизиты ниже (`practice` / `practice-only`) — вымышленные учебные, они
же лежат в `.env.example`. Настоящих секретов в этом файле быть не должно.

## Что это за проект

Учебный бэкенд магазина кроссовок: Python 3.11+, FastAPI, синхронный
SQLAlchemy 2.0, PostgreSQL 17, Alembic. API работает на компьютере,
PostgreSQL — в Docker Compose. Реальные секреты в переписке заменены
на `<REDACTED>`.

Задания модулей лежат в `prompts/` (тексты приложенных промптов).

## Окружение

| Что | Значение |
|---|---|
| Рабочая папка | `W:\sneaker_shop` |
| Интерпретатор | `W:\sneaker_shop\sneaker_shop_.venv\Scripts\python.exe` (Python 3.12.3, в venv только зависимости проекта) |
| Docker | Docker Desktop, Docker 26.0.0, Compose v2.26.1 |
| Рабочая БД | `sneaker_shop` (адрес в `.env`): `postgresql+psycopg://practice:practice-only@127.0.0.1:55433/sneaker_shop` |
| Контейнер | `sneaker-practice-m01-db` (сервис `db`, образ `postgres:17`, порт `127.0.0.1:55433 -> 5432`, том `pgdata`) |

## Текущее состояние (проверено фактически)

* Alembic: `alembic current` → `201c8afcccba (head)`; `alembic check` →
  `No new upgrade operations detected`. Ревизия не менялась с M1.
* В рабочей БД 8 таблиц: семь приложения (`brands`, `products`,
  `product_sizes`, `users`, `cart_items`, `orders`, `order_items`)
  плюс служебная `alembic_version`.
* Данные в рабочей БД: `brands=2`, `products=3`, `product_sizes=4`,
  `users=0`, `cart_items=0`, `orders=0`, `order_items=0`.
* Временных учебных БД нет (в кластере только `postgres`, `sneaker_shop`,
  `template0`, `template1`).
* API: версия приложения `0.2.0`, маршруты **только на чтение** —
  `GET /health`, `GET /brands`, `GET /products`, `GET /products/{id}`.
  У `/products` есть фильтры и пагинация: `brand_id`, `min_price`,
  `max_price`, `size`, `search`, `limit` (20, макс 100), `offset`.
  Записи (POST/PUT/PATCH/DELETE) не регистрируются до `require_admin` в M3.
* Порт 8000: ученик может держать на нём СВОЁ запущенное приложение
  (наблюдалось: `python.exe` PID 16020, путь `C:\Python312\python.exe` —
  системный Python, а не venv проекта). Это не осиротевший процесс, убивать
  его не нужно: для приёмки либо договориться о перезапуске, либо взять
  другой порт (`--port 8001`). Контейнер `db` запущен и healthy.

## Структура проекта

```
compose.yaml            сервис db (postgres:17), healthcheck pg_isready, том pgdata
.env / .env.example     вымышленные реквизиты practice / practice-only, DATABASE_URL
requirements.txt        зафиксированные fastapi, uvicorn, pydantic, SQLAlchemy, psycopg[binary], alembic, pydantic-settings, python-dotenv
alembic.ini             конфиг Alembic БЕЗ строки подключения (секретов нет)
alembic/env.py          URL берётся из Config.set_main_option, иначе из settings
alembic/versions/201c8afcccba_initial.py   единственная ревизия: семь таблиц
app/core/config.py      load_dotenv(override=False) + Settings.database_url (обязателен)
app/database.py         один Engine, фабрика Session, ping() с SELECT 1
app/main.py             FastAPI + GET /health + lifespan; подключает роутер каталога
app/schemas/catalog.py  Pydantic-схемы: BrandRead, ProductSizeRead, ProductRead (чтение) и ProductWrite (вход записи)
app/schemas/query.py    параметры запроса GET /products: ProductFilters + типы Money/SizeValue/Limit/Offset/Search
app/services/catalog.py чтение из БД: ProductFilters, list_brands, list_products (фильтры + limit/offset), get_product
app/routers/catalog.py  HTTP-маршруты каталога (только GET)
app/models/base.py      единственный DeclarativeBase
app/models/entities.py  семь моделей: Brand, Product, ProductSize, User, CartItem, Order, OrderItem
app/models/__init__.py  экспорт Base и семи моделей
app/seed.py             python -m app.seed: идемпотентный каталог (2/3/4)
prompts/                тексты заданий модулей + этот файл
```

Служебные (вспомогательные, не часть приложения; в `.gitignore`):
`_review_m01_probe.py`, `_http_acceptance.ps1`, `_seed_probe.py`,
`_seed_acceptance.ps1`, `_models_probe.py`, `_orders_probe.py`,
`_schema_probe.py`, `_lifespan_probe.py`, `_acceptance_probe.ps1`,
`_catalog_acceptance.ps1`, `_logging.json`, `_product_write_probe.py`,
`check_env.py`, `sql/`.

## Путь запроса (архитектура M2)

```
HTTP GET /products/10
  app/routers/catalog.py    get_product()   — только HTTP: 404, response_model
  app/services/catalog.py   get_product()   — только БД: select + selectinload
  app/models/entities.py    Product, ProductSize
  app/schemas/catalog.py    ProductRead      — контракт JSON (Decimal -> строка)
```

Правило разделения: в роутере нет SQL, в сервисе нет HTTP-ошибок, в схеме
нет ORM-логики. Сессия приходит через `Depends(get_session)` из
`app/database.py` (по одной на запрос, закрывается автоматически).

## БД: устройство и правила

* Схема создаётся **только** миграциями Alembic. `create_all` не
  вызывался ни разу и вызывать его не нужно.
* `alembic/env.py` уважает URL, заданный программно:
  `Config.set_main_option("sqlalchemy.url", ...)` главнее значения из
  `.env`. Это позволяет направлять миграции в другую БД (нужно для
  проверки «с нуля»). Раньше строка перезаписывалась безусловно — это был
  дефект, из-за которого миграция уходила в рабочую БД.
* Проверка «с нуля» делается **в отдельной временной БД**, а не удалением
  тома: `docker compose down -v` и `DROP DATABASE sneaker_shop` не
  используются.
* Временные БД создаются с именем вида `sneaker_review_<random>` и
  удаляются только они.

## Как запускать

```powershell
# из W:\sneaker_shop
docker compose up -d --wait                 # БД healthy
. .\sneaker_shop_.venv\Scripts\Activate.ps1 # или полный путь к python.exe
python -m alembic upgrade head              # применить миграции
python -m app.seed                          # каталог (повторный запуск безопасен)
python -m uvicorn app.main:app --reload --port 8000
```

## Как проверять

```powershell
# 1) схема и данные на новой временной БД (создаёт её сам, случайное имя)
$env:DATABASE_URL / $env:POSTGRES_USER / $env:POSTGRES_PASSWORD — из .env
.\sneaker_shop_.venv\Scripts\python.exe -X utf8 _review_m01_probe.py --phase setup
#    затем HTTP: powershell -NoProfile -File _http_acceptance.ps1
#    затем уборка:
.\sneaker_shop_.venv\Scripts\python.exe -X utf8 _review_m01_probe.py --phase teardown

# 2) seed идемпотентность (создаёт и удаляет свою БД)
.\sneaker_shop_.venv\Scripts\python.exe -X utf8 _seed_probe.py

# 3) HTTP-приёмка каталога M2 (API должен быть уже запущен отдельно)
#    API: python -m uvicorn app.main:app --port 8000 --log-config _logging.json
powershell -NoProfile -File _catalog_acceptance.ps1

# 4) контракт записи ProductWrite (БД не нужна, HTTP-запись ещё закрыта)
.\sneaker_shop_.venv\Scripts\python.exe -X utf8 _product_write_probe.py

# 5) фильтры и пагинация GET /products (API запущен отдельно на --port 8001)
.\sneaker_shop_.venv\Scripts\python.exe -X utf8 _catalog_filters_probe.py --port 8001 --database sneaker_shop
.\sneaker_shop_.venv\Scripts\python.exe -X utf8 _filters_empty_db_probe.py   # пустой каталог + 422
```

Ожидаемые результаты: `_review_m01_probe.py --phase setup` → `ALL SETUP
CHECKS PASSED`; `_http_acceptance.ps1` → `200 {"status":"ok"}` →
`503 {"status":"unavailable"}` → `200 {"status":"ok"}` на одном процессе
API; `--phase teardown` → `TEARDOWN OK`; `_catalog_acceptance.ps1` →
`/brands` = North, City; `/products` = ids 10, 20, 30 с ценами-строками;
`/products/999` = 404; записи = 405; снимок БД до/после совпадает.

## Правила и особенности, которые легко забыть

* **Alembic хранит служебную таблицу `alembic_version`** сверх семи
  таблиц: в проверках «сколько таблиц» их 8, а не 7.
* **`alembic check` не заменяет** чтение ограничений и отрицательные
  INSERT: он сравнивает только структуру, а работает ли CHECK/FK/UNIQUE —
  видно лишь на отклонённых вставках.
* **`ON CONFLICT (id) DO NOTHING` в seed** пропускает существующие строки
  и не перезаписывает изменённые `price`/`stock`. Конфликт по другому
  UNIQUE (`brands.name`) не подавляется: seed падает с `SeedError` и
  откатывает транзакцию.
* **HTML-кодировка консоли Windows:** логи и сообщения на русском без
  `PYTHONUTF8=1` выглядят кракозябрами. Это дефект отображения.
* **`sql/fresh_db.sh`** запускается только формами:
  `docker compose exec -T -e FRESH_DB=<имя> db sh -s create < sql\fresh_db.sh`
  (через `cmd /c`, с файловым redirect). Имя БД передаётся переменной, не
  аргументом; `psql` внутри скрипта получает `</dev/null`.
* **Docker CLI из песочницы агента** требует расширения прав
  (`danger-full-access`): доступ к демону идёт через named pipe
  `//./pipe/docker_engine`, закрытый в режиме `workspace-write`.
* **PowerShell 5.1:** `Start-Process -Environment` отсутствует — переменные
  окружения задаются в текущем процессе перед запуском дочернего.
  Скрипты `.ps1` с кириллицей надо сохранять с BOM, иначе парсер ломается.
* **SQL-логгер включается не через `uvicorn --log-level`**, а конфигом
  логирования: `--log-config _logging.json` (в нём `sqlalchemy.engine` =
  INFO). Только так видно реальные запросы и можно доказать отсутствие N+1.
* **`httpx` в venv нет** (и PyPI из песочницы недоступен), поэтому
  `fastapi.testclient.TestClient` не используется: HTTP-проверки идут
  против реального `uvicorn` через `curl.exe`. Если понадобится
  `TestClient`, `httpx` придётся доустановить вне песочницы.
* **Чтение через сессию закрывается `ROLLBACK`** в логе SQLAlchemy
  (`BEGIN (implicit)` … `ROLLBACK`): это нормально, изменений нет.
* **`price` и `size` в JSON — строки** (`"7990.00"`, `"42.0"`): так
  задумано, `json_encoders` в схемах переводит `Decimal` в строку без
  потери точности. Проверять в тестах надо строку, а не число.
* **PostgreSQL молча округляет `numeric(10,2)`**: `0.001` превратится в
  `0.00`, `10.999` — в `11.00`. Поэтому три знака после точки отклоняет
  сам `ProductWrite` (`_validate_price`), иначе клиент получил бы товар по
  цене, которую не называл. Проверено: `SELECT 0.001::numeric(10,2)` → `0.00`.
* **`1E+2` в `price` принимается** и означает ровно 100 (дробных знаков
  нет) — это не ошибка. Отклоняются `NaN`, `Infinity`, `0`, отрицательные,
  три знака после точки и значения ≥ 100000000.
* **`search` имеет тип `str | None`, и это обязательно**: `_strip`
  превращает строку из одних пробелов в `None`, а Pydantic отверг бы
  собственный результат, если объявить поле просто `str` (было 422 вместо
  «фильтр не задан»).
* **Приёмочные прогоны не должны зависеть от занятости порта**: ученик
  может держать своё приложение на 8000, поэтому вспомогательные серверы
  для приёмки поднимаются на 8001.

## Что уже проверено (и чем)

* M0.1: `docker compose config --quiet`, healthy, `SELECT 1` из psql,
  сохранность данных после перезапуска (том `pgdata`).
* M0.2: `/health` 200 → 503 (только сервис `db` остановлен) → 200 на том
  же процессе API, без утечки URL/пароля/traceback; `engine.dispose()` при
  штатном завершении.
* M1: модели (7 таблиц, 7 FK, 4 UNIQUE, 8 именованных CHECK), ревизия
  `201c8afcccba` (7 `create_table`, обратный порядок `downgrade`),
  `seed` идемпотентен, 12 отрицательных INSERT отклонены по имени
  ограничения, ORM-путь `Product(10).brand.name` и `.sizes`.
* Ревью-приёмка M0–M1 (последний прогон): все проверки пройдены,
  найден и исправлен дефект `alembic/env.py`.
* M2: `_catalog_acceptance.ps1` — `/brands` (North, City), `/products`
  (ids 10/20/30, цены строками, размеры у каждого), `/products/10`
  (42.0/stock 3 и 43.0/stock 0), `/products/20`, `/products/30`,
  `/products/999` → 404 `{"detail":"Product not found"}`,
  POST/PUT/PATCH/DELETE → 405, неизвестный `?brand_id=999` → 200 и все три
  товара (фильтр ещё не реализован), снимок БД до/после побайтово совпал
  (9 строк, включая `xmin`/`ctid`). SQL-лог: `/products` = один запрос за
  товарами + один `IN (10,20,30)` за размерами — N+1 нет.
* M3 (часть 1, контракт записи): `_product_write_probe.py` → все 44
  проверки пройдены. Валидные: пустые `description`/`image_url` дают `''`
  и `null`, `name` обрезается `strip`, `0.01`, `99999999.99`, 2000 символов
  описания и `brand_id=2147483647` принимаются. Невалидные → 422 с ошибкой
  в нужном поле: отрицательная/нулевая/слишком точная цена, `NaN`,
  `Infinity`, пробельное и пустое имя, имя > 120, `brand_id` ≤ 0,
  > int32, отсутствующий или нечисловой, неверный URL, лишние поля
  (`id`, `created_at`, `is_admin`, `sizes`), опечатка в имени поля.
  `HttpUrl` → `str` перед колонкой `Text`. Записи по HTTP по-прежнему
  закрыты: в OpenAPI только `get`.
* M3 (часть 2, фильтры и пагинация): `_catalog_filters_probe.py` —
  все проверки пройдены на рабочей БД и повторены на свежей временной
  (тот же seed, другая БД). `brand_id=1` → [10, 30]; цена 6000..8000 →
  [10], границы включительные (`min=5990` → [10,20,30], `max=5990` → [20]);
  `size=42` → [10, 30], `size=43` → [] (stock 0); `search=nOrTh` → [10, 30];
  `brand_id=1&size=42&max_price=8000` → [10]; `brand_id=999` → [];
  `search=%` → [] и `search=_` → [] (автоэкранирование); `limit=1` с
  `offset=0/1/2` → [10]/[20]/[30]; `offset=99` → [];
  422 на `limit=0`, `limit=101`, `offset=-1`, `size=-1`, `min_price=NaN`,
  `min_price=10.999`, `min_price>max_price` — и на уровне схемы, и по HTTP.
  SQL в логе: `WHERE EXISTS (SELECT 1 FROM product_sizes ...)` (без JOIN),
  `ORDER BY products.id` перед `LIMIT/OFFSET`,
  `ILIKE '%' || :name || '%' ESCAPE '/'`. `_filters_empty_db_probe.py`:
  на пустом каталоге все фильтры дают `[]`, 422 те же, данные
  восстановлены после `rollback`.

## Чего ещё нет

* Записи в каталог по HTTP: маршруты POST/PUT/PATCH/DELETE не
  зарегистрированы до `require_admin` (M3, часть 2). `ProductWrite` готов и
  проверен отдельно от HTTP.
* `passlib[bcrypt]`, `python-jose`, хеширование паролей, выдача JWT,
  `require_admin`, регистрация и вход — не установлены и не написаны.
* Обработки «`brand_id` не существует» → 409: контракт это пропускает
  (схема не ходит в БД), проверять будет сервис в M3, часть 2.
* Фильтра по цене «в диапазоне» через отдельный параметр нет — есть два
  (`min_price`/`max_price`), как в задании.
* Постраничности с `total` нет намеренно: массивы без обёртки. Состав
  страниц не гарантирован при параллельных вставках/удалениях
  (keyset-пагинация — отдельная задача).
* Корзины, оформления заказа, регистрации и JWT — нет.
* Каскадного удаления и обработчиков покупки нет намеренно.
* Не проверялись: конкурентное списание остатка, гонка при оформлении,
  `ON DELETE` на практике, `alembic downgrade` в последнем прогоне.

## Следующий шаг

M3, часть 3: поставить `passlib[bcrypt]` и `python-jose` (нужен доступ к
PyPI — из песочницы агента его нет, ставить вручную), сделать
хеширование пароля, регистрацию и вход, JWT и зависимость `require_admin`,
затем зарегистрировать POST/PUT/DELETE каталога только для админа, с
проверкой существования бренда (нет бренда → 409) и товара (нет товара при
PUT → 404). `ProductWrite` для этого уже готов. Дальше — корзина,
оформление заказа с транзакцией и контролем остатка.
