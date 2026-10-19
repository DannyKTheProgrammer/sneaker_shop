# STATE: учебный бэкенд магазина кроссовок

Язык общения с учеником: **только русский**. Английский — лишь по прямой
просьбе. Технические имена, команды и идентификаторы в коде остаются
латиницей, это не считается переходом на английский.

Файл для быстрого входа в контекст (в том числе в новой сессии агента).
Обновляется после каждого значимого шага. Последнее обновление: M4, часть 6
— ревью M4 по фактическому коду и поведению (`_m4_review_probe.py`):
дефектов поведения не найдено, найден и исправлен один дефект
ДОКУМЕНТАЦИИ (устаревшие комментарии про «следующую часть M4»), плюс три
замечания без изменения кода. Все 8 наборов M4 зелёные. Ниже добавлен
раздел «Входные условия M5 (checkout)» — checkout ещё не начат.

Данные ученика в рабочей БД: `users` — три строки (`danny@gmail.com`,
`admin@example.com` и `danny@gmail.comm`, созданная вручную в его
приложении на 8000). На момент записи `cart_items` в рабочей БД пуст:
ученик удалил свою тестовую строку сам. Его данные не удалять и не считать
мусором проверок.

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
  `orders=0`, `order_items=0`; в `users` — три записи, созданные учеником
  вручную (`danny@gmail.com`, `admin@example.com`, `danny@gmail.comm`);
  `cart_items=0` (свою тестовую строку ученик удалил сам). Это не мусор
  проверок: не удалять и не строить на «пустой рабочей БД» никаких
  утверждений (см. правило 17).
* Временных учебных БД нет (в кластере только `postgres`, `sneaker_shop`,
  `template0`, `template1`).
* API: две точки входа.
  `app.store_main:app` (он же `app.main:app`) — **результат M3 + шаги M4**,
  версия `0.8.0`: чтение каталога, регистрация/вход/JWT, запись каталога
  (`POST /products`, `PUT /products/{id}`, `DELETE /products/{id}`) под
  `require_admin` и корзина пользователя (`POST /cart/items`, `GET /cart`,
  `PATCH /cart/items/{item_id}`, `DELETE /cart/items/{item_id}`) под обычной
  аутентификацией.
  `app.catalog_main:app` — **контрольная точка M2**, версия `0.2.0`:
  те же чтение и auth, но маршрутов записи каталога нет (405), а корзины
  нет вовсе (404 — маршрут не зарегистрирован).
  У `/products` есть фильтры и пагинация: `brand_id`, `min_price`,
  `max_price`, `size`, `search`, `limit` (20, макс 100), `offset`.
* Порт 8000: ученик держит на нём СВОЁ приложение разработки
  (`python -m uvicorn app.main:app --reload --port 8000`, системный Python и
  reload-воркер). Это не осиротевший процесс, убивать его не нужно: для
  приёмки берём другой порт (8001 — store_main, 8002 — catalog_main) и
  СВОЮ временную БД. Контейнер `db` запущен и healthy.

## Структура проекта

```
compose.yaml            сервис db (postgres:17), healthcheck pg_isready, том pgdata
.env / .env.example     вымышленные реквизиты practice / practice-only, DATABASE_URL
requirements.txt        зафиксированные fastapi, uvicorn, pydantic, SQLAlchemy, psycopg[binary], alembic, pydantic-settings, python-dotenv
alembic.ini             конфиг Alembic БЕЗ строки подключения (секретов нет)
alembic/env.py          URL берётся из Config.set_main_option, иначе из settings
alembic/versions/201c8afcccba_initial.py   единственная ревизия: семь таблиц
app/app_factory.py      build_app(include_writes=...) — общая сборка: health, роутеры,
                        обработчики 422 и ShopError
app/store_main.py       app = build_app(include_writes=True)  — результат M3 + M4
app/catalog_main.py     app = build_app(include_writes=False) — контрольная точка M2
app/main.py             app = build_app(include_writes=True)  — совместимость с прежними командами запуска
app/core/config.py      load_dotenv(override=False); Settings: database_url и jwt_secret (SecretStr, оба обязательны),
                        jwt_algorithm=HS256, jwt_expire_minutes=15, jwt_issuer/audience
app/core/errors.py      ShopError (+ ShopNotFound 404, ShopConflict 409) и shop_error_handler:
                        в ответ уходит только detail — без SQL, имён ограничений и traceback
app/database.py         один Engine, фабрика Session, ping() с SELECT 1
app/main.py             FastAPI + GET /health + lifespan; подключает роутеры каталога и auth,
                        плюс обработчик 422 с маскировкой input/ctx (пароль не утекает)
app/schemas/catalog.py  Pydantic-схемы: BrandRead, ProductSizeRead, ProductRead (чтение) и ProductWrite (вход записи)
app/schemas/cart.py     CartItemAdd (вход: product_size_id + quantity, StrictInt 1..2147483647, extra=forbid),
                        CartItemUpdate (вход PATCH: только quantity, абсолютное значение),
                        CartItemRead (плоский ответ: id, product_size_id, product_id, name, size,
                        price, quantity, stock, subtotal; Decimal -> строка) и CartRead
                        (items + estimated_total — ориентировочная сумма)
app/schemas/query.py    параметры запроса GET /products: ProductFilters + типы Money/SizeValue/Limit/Offset/Search
app/core/security.py    пароли: CryptContext(bcrypt, rounds=12, truncate_error), validate_password,
                        hash_password, verify_password, dummy_hash (для входа без утечки по времени);
                        JWT: create_access_token / decode_access_token / InvalidToken (HS256, явный
                        algorithms, обязательные exp/sub/iss/aud, sub = ASCII-цифры в диапазоне int4)
app/schemas/auth.py     UserRegister/UserLogin (email+password, extra=forbid), UserRead, TokenResponse
app/services/catalog.py чтение и запись: ProductFilters, list_brands, list_products, get_product,
                        create_product, update_product, delete_product
                        (+ ошибки BrandNotFound/ProductNotFound/CatalogConflict/ProductDeletedConflict)
app/services/shop.py    корзина (M4): new_session() — СВЕЖАЯ сессия на операцию, add_cart_item
                        (замок владельца -> замок размера -> поиск пары -> проверка int4 и stock -> commit),
                        _load_cart_item с selectinload(CartItem.product_size -> ProductSize.product),
                        to_read / _item_read, get_cart + _cart_rows (один SELECT с JOIN sizes и products,
                        ORDER BY cart_items.id, estimated_total по текущим ценам),
                        _get_owned_item (ОДИН SELECT: cart_items.id AND cart_items.user_id),
                        update_cart_item (абсолютное quantity, проверка stock), delete_cart_item;
                        ошибки CartItemNotFound/ProductSizeNotFound/CartStockConflict/CartConflict
app/services/auth.py    register_user (+ EmailAlreadyRegistered, разбор IntegrityError 23505),
                        authenticate_user (+ InvalidCredentials; dummy_hash для отсутствующего email),
                        get_user_by_id (проверка токена)
app/routers/catalog.py  HTTP-маршруты каталога (только GET, публичные)
app/routers/catalog_write.py  POST/PUT/DELETE каталога; весь роутер под dependencies=[Depends(require_admin)]
app/routers/cart.py     POST /cart/items (дельта), GET /cart, PATCH /cart/items/{item_id} (абсолютное
                        количество), DELETE /cart/items/{item_id} (204) — маршруты САМОГО пользователя
                        (без require_admin), зависимость get_current_user_id (только id из проверенного
                        токена; параметра user_id нет, а ItemId = Path(ge=1, le=2147483647) отсекает
                        нечисловой/нулевой id как 422)
app/routers/auth.py     POST /auth/register (201/409/422), POST /auth/login (200/401/422),
                        GET /auth/me (200/401), GET /auth/admin-check (200/401/403);
                        HTTPBearer + get_current_user/CurrentUser, get_current_user_id и require_admin/AdminUser
setup_jwt.py            генерация JWT_SECRET в .env без перезаписи существующего ключа
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
`_catalog_filters_probe.py`, `_filters_empty_db_probe.py`,
`_write_service_probe.py`, `_delete_service_probe.py`,
`_m2_contract_review.py`, `_auth_register_probe.py`, `_auth_login_probe.py`,
`_jwt_probe.py`, `_auth_guard_probe.py`, `_write_matrix_probe.py`,
`_m2_checkpoint_probe.py`, `_m3_review_probe.py`, `_cart_service_probe.py`,
`_cart_items_acceptance.ps1`, `_cart_get_service_probe.py`,
`_cart_get_acceptance.ps1`, `_cart_get_fixtures.py`, `_cart_isolation_probe.py`,
`_cart_patch_delete_probe.py`, `_cart_duplicate_probe.py`, `_m4_review_probe.py`,
`_pg_admin.py`,
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

## Путь запроса и транзакция корзины (M4)

```
HTTP POST /cart/items  {"product_size_id": 101, "quantity": 2}
  app/routers/cart.py       CurrentUserId (id из токена) — не ORM-объект
  app/services/shop.py      new_session()  — СВЕЖАЯ сессия на операцию
    1. SELECT ... FROM users WHERE id = :id FOR UPDATE            (замок владельца)
    2. SELECT ... FROM product_sizes WHERE id = :sid FOR UPDATE   (замок размера)
    3. SELECT ... FROM products WHERE id = :pid                   (имя и цена)
    4. SELECT ... FROM cart_items WHERE user_id, product_size_id  (есть строка?)
    5. INSERT (нет строки) или UPDATE quantity += delta           (дельта!)
    6. проверка: quantity <= 2147483647 и quantity <= stock       (иначе 409)
    7. COMMIT; затем перечитывание позиции с размером и товаром
  app/core/errors.py        ShopError -> JSON {"detail": ...} без SQL и имён ограничений
```

```
HTTP GET /cart            (заголовок Authorization, БЕЗ параметров)
  app/routers/cart.py       CurrentUserId — параметра user_id у маршрута нет
  app/services/shop.py      new_session()
    1. SELECT ... FROM users WHERE id = :id FOR UPDATE            (замок владельца)
    2. SELECT cart_items.*, product_sizes.*, products.*
         FROM cart_items
         JOIN product_sizes ON product_sizes.id = cart_items.product_size_id
         JOIN products      ON products.id      = product_sizes.product_id
        WHERE cart_items.user_id = :uid ORDER BY cart_items.id    (один запрос, 2 JOIN)
    3. DTO: items = [CartItemRead], estimated_total = сумма subtotal (Decimal)
    4. сессия закрывается (COMMIT не нужен: только чтение, в логе ROLLBACK)
  app/schemas/cart.py       CartRead {items, estimated_total}
```

```
HTTP PATCH  /cart/items/{item_id}  {"quantity": 3}      (абсолютное значение)
HTTP DELETE /cart/items/{item_id}                        (204, тело пустое)
  app/routers/cart.py       ItemId = Path(ge=1, le=int4): 'abc', '0', '-1' -> 422
  app/services/shop.py      new_session()
    1. SELECT ... FROM users WHERE id = :id FOR UPDATE               (замок владельца)
    2. SELECT ... FROM cart_items
        WHERE cart_items.id = :item_id AND cart_items.user_id = :uid  (ОДНА выборка!)
        -> None => CartItemNotFound (404), и это ОДИН И ТОТ ЖЕ ответ
           для «нет такой позиции» и «позиция чужая»
    3. PATCH:  SELECT ... FROM product_sizes WHERE id = :sid FOR UPDATE
               (замок размера, ради проверки остатка)
               quantity > stock -> 409, данные не меняются
               UPDATE cart_items SET quantity = :q         (замена, не прибавка)
       DELETE: без замка размера — остаток не меняется;
               DELETE FROM cart_items WHERE id = :item_id  (уже проверено, что своя)
    4. COMMIT; ответ PATCH — CartItemRead, ответ DELETE — 204 без тела
```

**Почему выборка позиции одна, а не «найти, потом сравнить владельца».**
Условие `id AND user_id` стоит в одном `WHERE`, поэтому: между проверкой и
использованием нет окна; нельзя забыть фильтр в одном из вызовов; чужой id
неотличим от несуществующего, и по ответу нельзя выяснить, есть ли у
кого-то позиция с таким id (иначе — «оракул существования»); и это один
запрос вместо двух. `FOR UPDATE` на самой строке не нужен: строку защищает
замок владельца, который взят первым шагом.

**Администратор — не исключение.** Маршруты корзины не используют
`require_admin` вообще: роль проверяется только на административных
маршрутах каталога (и будущей смены статуса заказа). Поэтому администратор
для чужой корзины — обычный пользователь: PATCH и DELETE чужой позиции дают
те же 404, что и у любого другого. Ни чтения, ни изменения чужих корзин
через API не существует.

**Почему сумма ориентировочная (`estimated_total`), а цена заказа
фиксируется позже.** Корзина — это намерение, а не покупка: в ней нет ни
строки `orders`, ни снимка цены. Поэтому `price`, `subtotal` и
`estimated_total` считаются по ТЕКУЩЕЙ цене `products.price`: администратор
поменял цену — поменялась и оценка (проверено фактически: 7990.00 → 8990.00,
сумма 21970.00 → 23970.00 без единой записи в БД). Окончательная сумма
появляется только при checkout, когда цена записывается снимком в
`order_items.price_at_purchase` и в `orders.total`; после этого история
покупки не зависит от будущих изменений каталога. Отсюда же правило:
`estimated_total` нельзя использовать как обязательство, счёт или отчёт —
для этого есть оформленный заказ.

**Почему своя сессия.** Зависимость аутентификации (`get_current_user`) уже
прочитала строку `users` в сессии из `get_session` и тем самым открыла
транзакцию и снимок данных (READ COMMITTED — снимок на момент первого
оператора). Если бы блокировку и проверку остатка делали в этой же
транзакции, мы опирались бы на снимок, сделанный ДО захвата блокировки:
типовая гонка «прочитал старое — записал новое». Поэтому корзина и checkout
открывают новую сессию (`SessionLocal()`), а из слоя аутентификации в неё
передаётся только `user.id`. ORM-объект из чужой сессии не передаётся.

**Порядок блокировок один для корзины и checkout:**
`users` FOR UPDATE → `product_sizes` по возрастанию id → (в checkout ещё
`products` FOR SHARE по возрастанию id). Единый порядок захвата исключает
взаимную блокировку; замки живут до COMMIT/ROLLBACK, то есть одну операцию.
Замок владельца сериализует операции одного пользователя, поэтому две
одновременные вставки не могут потерять одну из дельт; `UNIQUE(user_id,
product_size_id)` остаётся страховкой от дубля пары.

**Замок владельца берётся и на `GET /cart`** — по общему протоколу («сначала
users FOR UPDATE для всех операций корзины»). Для согласованности самого
ответа он не обязателен: один SELECT и так берёт единый снимок. Его эффект
другой — чтение дождётся уже начатой записи этого пользователя и вернёт
состояние ПОСЛЕ неё. Цена решения: чтение сериализуется с записями того же
пользователя (и с FK-проверками, которым нужна строка `users`). Если такая
свежесть не нужна, убирается один вызов `_lock_user` в `get_cart`; ничего
другого менять не нужно.

**Корзина не резервирует остаток:** `stock` не уменьшается ни на одном шаге
M4. Проверяется только `quantity <= stock`; списание — задача checkout, где
остаток перепроверяется под той же блокировкой (`UPDATE ... WHERE stock >=
:qty`).

**DTO собирается до закрытия сессии.** `get_cart`/`add_cart_item` возвращают
Pydantic-модели (Decimal/int/str), а не ORM-объекты: роутер закрывает сессию
сразу после возврата сервиса, и обращение к отсоединённому ORM-объекту дало
бы `DetachedInstanceError`. Проверено и это: в элементах `CartRead` нет
`_sa_instance_state`.

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

# 6) сервисы записи create_product/update_product (создаёт и удаляет СВОЮ временную БД)
.\sneaker_shop_.venv\Scripts\python.exe -X utf8 _write_service_probe.py

# 7) удаление товара delete_product (своя временная БД, заказ и корзина как ссылки)
.\sneaker_shop_.venv\Scripts\python.exe -X utf8 _delete_service_probe.py

# 8) независимое ревью контракта M2 (все чтения, фильтры, 404/409/422,
#    фикстур: товар с тремя размерами, stock=0, буквальные % и _)
.\sneaker_shop_.venv\Scripts\python.exe -X utf8 _m2_contract_review.py --port 8001 --database <имя>

# 9) регистрация POST /auth/register (API на 8001, своя временная БД)
.\sneaker_shop_.venv\Scripts\python.exe -X utf8 _auth_register_probe.py --port 8001 --database <имя>

# 10) security.py и POST /auth/login (хэши, соль, UTF-8 > 72 байт, 401 без утечек)
.\sneaker_shop_.venv\Scripts\python.exe -X utf8 _auth_login_probe.py --port 8001 --database <имя>

# 11) JWT: выдача, срок, подпись, iss/aud, GET /auth/me, схема HTTPBearer
.\sneaker_shop_.venv\Scripts\python.exe -X utf8 _jwt_probe.py --port 8001 --database <имя>

# 12) защита доступа: get_current_user и require_admin (матрица отказов, смена роли)
.\sneaker_shop_.venv\Scripts\python.exe -X utf8 _auth_guard_probe.py --port 8001 --database <имя>

# 13) МАТРИЦА ЗАПИСИ (результат M3, store_main на 8001, СВОЯ временная БД):
#     для POST/PUT/DELETE: без токена 401, неверный токен 401, обычный 403, админ 201/200/204,
#     плюс 404/409/422 и проверка неизменности данных после каждого отказа.
.\sneaker_shop_.venv\Scripts\python.exe -X utf8 _write_matrix_probe.py --port 8001 --database <имя>

# 14) контрольная точка M2 (catalog_main на 8002): запись даёт 405, чтение публичное
.\sneaker_shop_.venv\Scripts\python.exe -m uvicorn app.catalog_main:app --port 8002
.\sneaker_shop_.venv\Scripts\python.exe -X utf8 _m2_checkpoint_probe.py --port 8002

# 15) ревью M3 целиком: 4 типа доступа × 3 метода, невалидные JWT, роли, публичность чтений
.\sneaker_shop_.venv\Scripts\python.exe -X utf8 _m3_review_probe.py --port 8001 --database <имя>

# 16) КОРЗИНА, сервисный уровень (M4, часть 1; создаёт и удаляет СВОЮ временную БД):
#     дельта/повтор, stock0, отсутствующий размер, переполнение int4, чужой владелец,
#     реальный порядок блокировок из перехваченного SQL, ожидание замка, savepoint != commit
.\sneaker_shop_.venv\Scripts\python.exe -X utf8 _cart_service_probe.py

# 17) КОРЗИНА, HTTP-приёмка (M4, часть 1): сама создаёт временную БД, поднимает
#     store_main на 8001, регистрирует пользователя, проверяет 200/401/404/409/422,
#     порядок блокировок в логе сервера и неизменность рабочей БД, потом всё убирает.
powershell -NoProfile -File _cart_items_acceptance.ps1

# 18) ЧТЕНИЕ КОРЗИНЫ, сервисный уровень (M4, часть 2; создаёт и удаляет СВОЮ временную БД):
#     пустая корзина ([] и 0.00), две позиции в порядке id, subtotal/estimated_total,
#     ОДИН JOIN-запрос вместо N+1 (из перехваченного SQL), DTO после закрытия сессии,
#     ДВА пользователя, смена цены каталога и неизменность снимка order_items
.\sneaker_shop_.venv\Scripts\python.exe -X utf8 _cart_get_service_probe.py

# 19) ЧТЕНИЕ КОРЗИНЫ, HTTP-приёмка (M4, часть 2): своя временная БД + store_main на 8001,
#     два пользователя, ?user_id игнорируется, 401 без токена, смена цены через админский
#     PUT /products/10 (роль выдаётся SQL через _cart_get_fixtures.py), 405 на POST /cart.
powershell -NoProfile -File _cart_get_acceptance.ps1

# 20) ИЗОЛЯЦИЯ ВСЕХ ЧЕТЫРЁХ ОПЕРАЦИЙ КОРЗИНЫ (M4, часть 3): воспроизводимый сценарий
#     Аня / Борис / администратор. Своя временная БД, свой сервер на 8001, свой лог.
#     Проверяет: Борис видит [], PATCH и DELETE чужой позиции -> 404 у Бориса и у админа,
#     одинаковое тело отказа, состояние БД до/после отказов, четыре операции без JWT -> 401,
#     204/409/422 на своих данных, SQL «id AND user_id в одном WHERE», отсутствие
#     токенов и паролей в отчёте. Всё убирает за собой.
.\sneaker_shop_.venv\Scripts\python.exe -X utf8 _cart_isolation_probe.py

# 21) ДЕМОНСТРАЦИЯ PATCH/DELETE (M4, часть 4): своя временная БД и свой сервер 8001,
#     печатает фактическое состояние после каждого шага: POST qty2 -> PATCH qty1 ->
#     DELETE -> повторный DELETE 404; отказы 422 (0, -1, 1.5, "2", true, > int4,
#     нет поля, лишнее поле), 409 (4 при stock 3; остаток изменили после POST),
#     404 (чужой id у Бориса, несуществующий 999999), 401 без токена;
#     проверяет, что stock 101 остаётся 3 на всех шагах и заказов не появляется.
.\sneaker_shop_.venv\Scripts\python.exe -X utf8 _cart_patch_delete_probe.py

# 22) ПОВТОРНОЕ ДОБАВЛЕНИЕ ПАРЫ (M4, часть 5): своя временная БД, без HTTP.
#     1) ДВА ПОСЛЕДОВАТЕЛЬНЫХ POST qty=1 -> одна строка quantity=2, stock=3,
#        первый вызов INSERT, второй UPDATE; порядок блокировок из SQL.
#     2) UNIQUE(uq_cart_size) отклоняет прямой INSERT дубля пары (23505).
#     3) контрпример: два SELECT без блокировки + два UPDATE -> quantity=2
#        вместо 3 (потерянное обновление).
#     4) настоящий параллельный тест: соединение A держит users FOR UPDATE,
#        поток B выполняет shop.add_cart_item и ЖДЁТ (проверка через
#        pg_stat_activity/pg_locks), затем A коммитит, B видит свежие данные.
.\sneaker_shop_.venv\Scripts\python.exe -X utf8 _cart_duplicate_probe.py

# 23) РЕВЬЮ M4 ЦЕЛИКОМ (M4, часть 6): своя временная БД + свой сервер на 8001.
#     Четыре маршрута, DTO (ключи и типы), extra=forbid, строгие int и границы
#     int4, 401/404/409/422/405, владелец только из JWT, принадлежность
#     (включая админа), UNIQUE пары и увеличение, абсолютный PATCH, пустой 204,
#     отсутствие резерва остатка, состояние БД до/после КАЖДОГО отказа,
#     проверки по исходникам (begin_nested, create_all, Depends(get_session),
#     устаревшие комментарии), схема и ревизия.
.\sneaker_shop_.venv\Scripts\python.exe -X utf8 _m4_review_probe.py
```

Ключ подписи: `python setup_jwt.py` (создаёт `JWT_SECRET` в `.env`, повторно
не перезаписывает, значение не печатает). Без ключа приложение не стартует.

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
* **Владелец транзакции один — сервис.** `create_product`/`update_product`
  сами делают `commit` и `rollback`; роутер не коммитит. `with
  session.begin()` использовать нельзя: зависимость уже открыла транзакцию
  (autobegin), и повторный `begin()` даёт `InvalidRequestError`. Проверено:
  `s.in_transaction()` → False, после первого запроса → True, `commit()`
  без `begin()` работает.
* **Маскировать исключения нельзя.** В `_commit` ловится только
  `IntegrityError` → `CatalogConflict`; `RuntimeError` и прочие всплывают
  как есть (проверено отдельным тестом).
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
* M3 (часть 3, сервисы записи): `_write_service_probe.py` → все проверки
  пройдены в отдельной временной БД. `create_product`: новый `id=31`
  (свободен после seed), `name` с обрезкой пробелов, `price` точно
  `12345.67`, `description=''`, `image_url` строкой, `sizes=[]`,
  `created_at` заполнен сервером. `update_product(10, ...)`: поля заменены,
  пропущенные `description`/`image_url` сброшены к `''`/`null` (PUT — не
  PATCH), размеры `[(101,42.0,3),(102,43.0,0)]` не тронуты, `created_at`
  сохранён. Отказы: `BrandNotFound` без создания/изменения товара,
  `ProductNotFound` для PUT несуществующего, FK-конфликт → `IntegrityError`
  → `CatalogConflict`, `RuntimeError` не замаскирован. Смена цены товара
  не изменила `price_at_purchase=999.00` старой позиции заказа.
* M3 (часть 4, удаление): `_delete_service_probe.py` → все проверки
  пройдены в отдельной временной БД. Товар 10 с заказом на размер 101 —
  `ProductDeletedConflict`, товар, оба размера, позиция заказа и её
  `price_at_purchase` целы. Товар 20 со ссылкой ТОЛЬКО из корзины — тоже
  отказ, строка корзины не тронута. Гонка (ссылка появилась после
  проверки) → `IntegrityError` → `ProductDeletedConflict`, полный откат,
  сессия рабочая. После снятия ссылок товар 10 удалён вместе с размерами
  101/102, чужие размеры (201, 301) и корзина не тронуты, повторное
  удаление → `ProductNotFound`. FK в схеме остались те же 7, `ON DELETE
  CASCADE` нигде нет.
* Ревью M2 (`_m2_contract_review.py`, отдельная временная БД, независимый
  SQL для ожиданий): все проверки пройдены. Подтверждено: все публичные
  чтения и ключи контракта; `Decimal` строками; 21 отказ 422 и 9 приёмов на
  границах; AND-фильтры совпали с SQL; `stock > 0` (товар с нулевым
  остатком не попадает); буквальные `%` и `_` (товар «100% cotton»
  находится по `%`, «w_ld» — по `_`, а `wXld` — нет); товар с тремя
  размерами выдан ровно один раз; страницы по 2 без пропусков и дублей;
  404 на 999/0/-1; 405 на POST/PUT/PATCH/DELETE; удаление товара со
  ссылкой из заказа — конфликт с сохранением товара, размеров и истории.
  Мелкие правки по итогам: убраны неиспользуемые импорты `AfterValidator`
  (`schemas/catalog.py`, `schemas/query.py`), обновлён устаревший заголовок
  `routers/catalog.py`. После правок все три набора (ревью, фильтры, запись)
  перезапущены — `ALL CHECKS PASSED`.
* M3 (часть 5, регистрация): `_auth_register_probe.py` → все проверки
  пройдены. `POST /auth/register` → 201, ответ только
  `{id, email, is_admin=false}`; email нормализуется strip+lower и в ответе,
  и в БД; в БД хэш `$2b$12$...`, открытого пароля нет, `verify_password`
  проходит и на неверном пароле даёт False; занятый email (тот же, другой
  регистр, пробелы) → 409 и второй пользователь не создаётся; 12 наборов
  невалидных данных → 422 (включая `is_admin=true`, `id`,
  `hashed_password`, 73 символа, 40 кириллических символов = 80 байт, NUL);
  **пароль не появляется в теле 422**, ошибки содержат только
  type/loc/msg; неудачные попытки не создают строк; повторная регистрация
  того же нормализованного email на уровне сервиса → `EmailAlreadyRegistered`
  (UNIQUE в БД, а не предварительный SELECT); маршрута login и назначения
  администратора нет. Регрессия M2 после этого прогнана — `ALL CHECKS PASSED`.
* M3 (часть 6, вход и security.py): `_auth_login_probe.py` → все проверки
  пройдены. `app/core/security.py`: правильный пароль → True, неверный →
  False, пустой и битый хэш → False (без исключения), два хэша одного
  пароля различаются (соль), оба проверяются; ASCII 73 символа и кириллица
  40 символов (80 байт) отклоняются **до** хэширования (`PasswordTooLong`),
  кириллица 20 символов (40 байт) принимается; NUL отклонён. `dummy_hash`
  стабилен. `POST /auth/login`: верная пара → 200
  (`access_token=null`, `token_type=none`, `user` без хэша), неверный пароль
  и отсутствующий email → одинаковые 401 `Invalid email or password` с
  `WWW-Authenticate: Bearer`; 422 без утечки пароля (в том числе кириллица
  80 байт). Замеры: bcrypt 208 мс против хэша пользователя и 207 мс против
  dummy (разница < 1%), средние времени ответа 212 и 225 мс.
  Отказы не изменили данные. Все прежние наборы перезапущены после
  обновления ожиданий (появился `/auth/login`) — `ALL CHECKS PASSED`.
* M3 (часть 7, JWT): `_jwt_probe.py` → все проверки пройдены. Настройки:
  секрет из окружения/`.env` (64 символа), `SecretStr` не показывает значение
  в repr и в ошибках валидации; без `JWT_SECRET` — `ValidationError:
  jwt_secret Field required`, при коротком (< 32) — «JWT_SECRET must be at
  least 32 characters; generate one with `python setup_jwt.py`».
  `setup_jwt.py` в отдельном каталоге: без `.env` — понятная ошибка, с
  пустым ключом — сгенерировал 66 символов, повтор не изменил файл
  (хэш совпал). Вход отдаёт настоящий JWT: `alg=HS256`, `typ=JWT`,
  `sub=str(user.id)`, `iss=sneaker-practice`, `aud=sneaker-api`, срок 900 c,
  в claims только `aud/exp/iat/iss/sub` (нет пароля, хэша, роли, email),
  `token_type=bearer`. `GET /auth/me`: 200 с валидным токеном, 401 и
  `WWW-Authenticate: Bearer` без заголовка/с мусором, при изменённом
  payload, чужом ключе, чужом `iss`/`aud`, отсутствии `sub`, после истечения
  (токен на 1 с принят, через 2 с — 401) и для удалённого пользователя.
  OpenAPI: схема `HTTPBearer` (OAuth2 нет), `/auth/me` защищён. Все прежние
  наборы после обновления ожиданий — `ALL CHECKS PASSED`.
* M3 (часть 8, защита доступа): `_auth_guard_probe.py` → все проверки
  пройдены. `get_current_user`: 200 с валидным токеном; 401 +
  `WWW-Authenticate: Bearer` при отсутствии заголовка, `Basic`, заголовке
  без `Bearer`, пустом/мусорном токене, подписи чужим ключом, изменённом
  payload, `alg=HS512`, просроченном и отсутствующем `exp`, отсутствующих
  `sub`/`iss`/`aud`, чужом `iss`/`aud`, `sub` не числом, с плюсом,
  пробелом, Unicode-цифрами, больше int4, отрицательном, числом вместо
  строки, и для удалённого пользователя. `require_admin`: обычный
  пользователь → 403, администратор → 200, без токена → 401 (не 403);
  `X-Admin` и `?is_admin=true` не помогают. Смена `is_admin` в БД
  действует на уже выданный токен сразу (403 → 200 → 403 без перевыпуска),
  а `is_admin=true` в claims ничего не даёт: роль читается из БД.
* M3 (часть 9, запись каталога): `_write_matrix_probe.py` → все проверки
  пройдены. Для **каждого** из POST /products, PUT /products/{id},
  DELETE /products/{id}: без токена → 401 (+ `WWW-Authenticate`), неверный
  токен → 401, заголовок без `Bearer` → 401, обычный пользователь → 403,
  администратор → 201/200/204. Успешный DELETE отдаёт 204 с пустым телом,
  повторный → 404. Отдельно: PUT несуществующего → 404, неизвестный бренд
  в POST и PUT → 409, DELETE товара со ссылкой из заказа (размер 101) → 409,
  со ссылкой только из корзины (размер 201) → 409, цена `-1` и `10.999` и
  лишнее поле → 422. После **каждого** отказа снимок
  products/product_sizes/cart_items/orders/order_items не изменился.
  PUT — полная замена (description → `''`, image_url → `null`) и размеры
  товара не трогает. Контрольная точка M2 (`_m2_checkpoint_probe.py`,
  catalog_main): каталог только GET, POST/PUT/DELETE/PATCH → 405, чтение
  публичное, регистрация и вход работают.
* Ревью M3 (`_m3_review_probe.py`, своя временная БД, 51 реальный HTTP-запрос
  с журналом): дефектов не найдено. Проверено фактически: ответы и ошибки не
  содержат открытого пароля и `hashed_password`; в 422 нет `input`/`ctx` и
  значения пароля; `is_admin` из тела, query и claims доступа не даёт;
  отказы по подписи, `alg=none`, `HS512`, `exp`, `iss`, `aud`, `sub`
  (не число / Unicode / > int4 / отрицательный / не строка) и удалённому
  пользователю → 401; отсутствие токена → 401 (не 403), `WWW-Authenticate`
  есть; `require_admin` на всех трёх методах; повышение и отзыв роли
  действуют на уже выданный токен (403 → 200 → 403); повтор email → 409,
  неверный пароль и неизвестный email → одинаковый 401; регистрация не
  принимает `is_admin`; чтения (`/health`, `/brands`, `/products`,
  `/products/10`, `/openapi.json`, `/docs`) публичны и данных не меняют;
  Swagger: единственная схема `HTTPBearer`, логин — `application/json`,
  все три маршрута записи помечены защищёнными.
* M4 (часть 1, `POST /cart/items`): `_cart_service_probe.py` → все
  проверки пройдены в отдельной временной БД, `_cart_items_acceptance.ps1`
  → `ALL CART HTTP CHECKS PASSED` (сервер store_main на 8001 со своей
  временной БД, потом уборка). Фактически проверено: первый вызов создаёт
  позицию с `quantity=delta` (2 → 200, `id`, `product_size_id=101`,
  `product_id=10`, `name='North Runner'`, `size='42.0'`, `price='7990.00'`,
  `stock=3`, `subtotal='15980.00'`), повторный прибавляет дельту (2 → 3,
  та же строка, `subtotal='23970.00'`); `stock` после всех вызовов остался
  3 (корзина не резервирует). Отказы: `stock=0` (размер 102) → 409,
  количество сверх остатка (4 при stock 3) → 409, отсутствующий размер
  9999 → 404, `quantity=0`, `quantity=null`, `"5"`, `1.5`, `true`,
  `2147483648`, `product_size_id=0`, лишнее поле (`user_id`, `price`),
  пустое тело → 422, без токена и с мусорным токеном → 401. Второй
  пользователь получает свою строку (другой `id`), корзина первого не
  меняется. SQL в логе сервера: `SELECT ... FROM users ... FOR UPDATE OF
  users` (первым), затем `SELECT ... FROM product_sizes ... FOR UPDATE OF
  product_sizes`. Сервисная проба подтверждает независимо: перехваченный
  порядок операторов `users FOR UPDATE → product_sizes FOR UPDATE →
  products → cart_items → INSERT/UPDATE`, реальный `after_commit`,
  ожидание второй транзакции на замке владельца (`statement_timeout`
  срабатывает), переполнение int4 → 409 вместо 500, `begin_nested` без
  `commit` данные не сохраняет, `UNIQUE(uq_cart_size)` отклоняет дубль
  пары, несуществующий владелец → `ShopError` без строк, схема не
  изменилась (8 таблиц, 7 FK, ревизия `201c8afcccba`), рабочая БД после
  прогона не изменилась (`cart_items=0`, `stock=3`).
* M4 (часть 2, `GET /cart`): `_cart_get_service_probe.py` →
  `ALL CART GET SERVICE CHECKS PASSED` (своя временная БД),
  `_cart_get_acceptance.ps1` → `ALL CART GET HTTP CHECKS PASSED` (store_main
  на 8001, своя временная БД, два пользователя, уборка). Фактически
  проверено: пустая корзина → 200 `{"items":[],"estimated_total":"0.00"}`
  (в JSON именно строка `"0.00"`, не число); у пользователя A две позиции
  (201×1 и 101×2, добавлены в обратном порядке) приходят в порядке
  возрастания `cart_items.id`, а не по цене или размеру; `price`/`subtotal`
  = `"5990.00"`/`"5990.00"` и `"7990.00"`/`"15980.00"`,
  `estimated_total="21970.00"`; у пользователя B своя корзина
  (`"7990.00"`, другой id строки), корзина A не изменилась. `?user_id=<чужой>`
  игнорируется в обе стороны (владелец только из токена), без токена и с
  мусорным токеном → 401, `POST /cart` → 405. Смена цены каталога через
  настоящий админский `PUT /products/10` (7990.00 → 8990.00): в корзине
  появились `price="8990.00"`, `subtotal="17980.00"`, `estimated_total` A
  стал `"23970.00"`, у B — `"8990.00"`, количество и строки корзины не
  менялись, остаток остался 3, а уже созданная позиция заказа сохранила
  снимок `price_at_purchase=7990.00` (корзина — оценка «на сейчас», заказ —
  история). `estimated_total` — `Decimal`, не float. SQL: ровно два SELECT
  на запрос (`users ... FOR UPDATE OF users`, затем ОДИН оператор
  `FROM cart_items JOIN product_sizes JOIN products ... WHERE
  cart_items.user_id = ... ORDER BY cart_items.id`) — N+1 нет; DTO собирается
  до закрытия сессии и после закрытия читается, в элементах нет
  `_sa_instance_state`. Схема не менялась (8 таблиц, 7 FK,
  `201c8afcccba`), рабочая БД после прогона: `cart_items=0`,
  `price(10)=7990.00`, временных БД не осталось. Регрессия: `POST`-наборы
  части 1 (`_cart_service_probe.py`, `_cart_items_acceptance.ps1`)
  перезапущены после рефакторинга `to_read` → `_item_read` — зелёные.
  Примечание: проверка «в рабочей БД нет корзин» позже оказалась неверной
  и заменена (см. правило 17) — ученик работает в этой БД сам.
* M4 (часть 3, `PATCH`/`DELETE` + изоляция): `_cart_isolation_probe.py` →
  `ALL CART ISOLATION CHECKS PASSED` (своя временная БД, свой сервер на
  8001, свой лог; сценарий: Аня добавляет size 101 ×2, Борис видит `[]` и
  `"0.00"`, админ видит `[]`). Фактически проверено: Борис `PATCH` и
  `DELETE` по id позиции Ани → 404 с телом `{"detail":"Cart item not
  found"}`; администратор — те же 404 и то же тело (роль не даёт доступа к
  чужим корзинам); полное состояние пяти таблиц (`cart_items`, `users`,
  `product_sizes`, `orders`, `order_items`, сравнение `to_jsonb`) до и после
  четырёх отказов совпало, позиция Ани осталась `quantity=2`, остаток
  size 101 — 3. Свои данные работают: Борис `PATCH` своей позиции → 200
  (quantity 4, `subtotal="23960.00"` по цене 5990.00), `PATCH` до 6 при
  остатке 5 → 409 и количество осталось 4, `DELETE` своей → 204 с пустым
  телом; Аня `PATCH` до 3 (`subtotal="23970.00"`), до 4 → 409 (состояние не
  изменилось), `quantity=0` → 422, лишнее `user_id` → 422, `DELETE` → 204,
  повторный → 404. Без JWT: `GET /cart`, `POST /cart/items`,
  `PATCH /cart/items/999999`, `DELETE /cart/items/999999` → 401, состояние
  не изменилось. Несуществующий id 999999 → 404, `abc`/`0`/`-1` → 422.
  SQL в логе сервера: 13 выборок с `WHERE cart_items.id = ... AND
  cart_items.user_id = ...` — то есть каждая операция PATCH/DELETE берёт
  позицию одной выборкой (единственные SELECT-ы «только по id» — это ответ
  POST /cart/items, который читает уже проверенную строку). В отчёте
  проверено отсутствие токенов и пароля. После прогона в рабочей БД нет
  пользователей этого прогона (проверка по email с суффиксом), временная
  БД удалена. Все прежние наборы M4 перезапущены — зелёные.
* M4 (часть 4, демонстрация PATCH/DELETE): `_cart_patch_delete_probe.py` →
  `ALL CART PATCH/DELETE CHECKS PASSED` (своя временная БД, свой сервер
  8001, в отчёте нет токенов и пароля). Фактически снятые состояния:
  `cart_items=[]` → POST qty2 → `[(1, 101, 2)]` → PATCH qty1 →
  `[(1, 101, 1)]` (АБСОЛЮТНОЕ значение, не 3) → DELETE 204 с пустым телом →
  `[]` → повторный DELETE 404; `stock101=3` во всех снимках, `orders=0`,
  `order_items=0`. Отказы 422: `quantity=0`, `-1`, `1.5`, `"2"`, `true`,
  `2147483648`, отсутствующее поле, лишнее `user_id` — количество и
  остатки не менялись. 409: `quantity=4` при `stock=3` (количество
  осталось 1). 404: чужой id у Бориса (PATCH и DELETE), несуществующий
  999999 у Ани — состояние то же. 401 без токена на PATCH и DELETE.
  Отдельно проверено, что PATCH видит АКТУАЛЬНЫЙ остаток: после
  `UPDATE product_sizes SET stock = 0 WHERE id = 201` (внешнее изменение)
  PATCH даёт 409 «Only 0 item(s)…», а после возврата `stock=5` — 200 и
  `quantity=5`; остаток при этом не списывается (в корзине лишь
  фиксируется намерение).
* M4 (часть 5, повторное добавление пары): `_cart_duplicate_probe.py` →
  `ALL DUPLICATE-ADD CHECKS PASSED`. (1) Два **последовательных** POST
  qty=1 → одна строка `[(1, 101, 2)]`, `stock=3`, id строки не изменился;
  первый вызов — `INSERT INTO cart_items`, второй — `UPDATE cart_items SET
  quantity=…` (не INSERT). Порядок операторов: `users FOR UPDATE` →
  `product_sizes FOR UPDATE` → `products` (чтение) → `cart_items` (поиск
  пары) → `INSERT`/`UPDATE`; два SELECT в конце — перечитывание позиции для
  ответа. Это НЕ конкурентный тест. (2) Прямой INSERT дубля пары отклонён:
  `sqlstate=23505, constraint=uq_cart_size`, данные не изменились. (3)
  Контрпример без блокировки: A и B прочитали `quantity=1`, обе записали
  2 (B ждала строковую блокировку A и применила УЖЕ УСТАРЕВШЕЕ значение) →
  в строке 2 вместо 3, то есть потерянное обновление. (4) Настоящий
  параллельный тест: A держала `users FOR UPDATE` и незакоммиченную строку
  `quantity=1`; `pg_stat_activity` по pid соединения B показал
  `('active', 'Lock', 'transactionid')`, `pg_locks` — 1 невыданную
  блокировку, поток B был жив; после `COMMIT` A поток завершился без
  ошибки, прождав 0.92 c, и получил `quantity=2` (одна строка, `stock=3`).
  (5) Схема не менялась: 8 таблиц, 7 FK, 4 UNIQUE (включая `uq_cart_size`),
  ревизия `201c8afcccba`. Временная БД удалена, рабочая не тронута.
* M4 (часть 6, ревью): `_m4_review_probe.py` → `ALL M4 REVIEW CHECKS
  PASSED` (своя временная БД и свой сервер 8001; ~90 HTTP-запросов).
  Подтверждено фактически: четыре маршрута; `401` без токена и с мусорным
  токеном на всех четырёх, всегда с `WWW-Authenticate: Bearer`; `405` на
  `POST /cart`, `GET /cart/items`, `PUT /cart/items/{id}`; DTO — ровно 9
  ключей позиции и `{items, estimated_total}` у корзины, `size`/`price`/
  `subtotal` строками (`"5990.00"`, `"11980.00"`), `quantity`/`stock`
  целыми, `user_id`/`hashed_password` в ответе нет; пустая корзина —
  `{"items":[],"estimated_total":"0.00"}`; `GET /cart?user_id=1` с токеном
  Бориса отдаёт ЕГО пустую корзину; `extra=forbid` — лишние `user_id`,
  `price`, `product_size_id` дают 422; `quantity` 0, −1, 1.5, "2", true,
  2³¹, null и отсутствие поля → 422 (и для POST, и для PATCH);
  `product_size_id` 0, −1, 2³¹, "201" → 422; id в пути `abc`/`0`/`-1`/
  `2147483648`/`1.5` → 422; чужой id — 404 и у Бориса, и у **админа**;
  несуществующий — 404; тела ошибок не содержат `psycopg`, имён
  ограничений, `SELECT`, `Traceback`, `hashed_password`; `stock=0` и
  превышение остатка (PATCH 6 при 5, POST +4 при остатке 2) → 409; повтор
  POST 2+1=3 без второй строки; PATCH 5 — АБСОЛЮТНО (не 8), `subtotal`
  `"29950.00"`; DELETE 204 с пустым телом и без `Content-Length`-тела,
  повторный → 404; состояние БД (строки корзины, 4 остатка, `orders`,
  `order_items`) сравнивалось до/после КАЖДОГО из ~40 отказов — изменений
  нет; итог: `stock` 101=3, 102=0, 201=5, 301=2, `orders=0`,
  `order_items=0`; схема — 8 таблиц, 7 FK, 4 UNIQUE, ≥8 CHECK, ревизия
  `201c8afcccba`. По исходникам: нет `begin_nested(`, нет
  `UPDATE product_sizes`/`SET stock`, роутер не берёт `Depends(get_session)`
  и не использует `Depends(require_admin)`, во всём `app/` нет вызова
  `create_all(`, все четыре декоратора на месте, `extra="forbid"` в двух
  схемах, `StrictInt` + `le=2147483647`. **Найден один дефект** —
  устаревшие комментарии (см. ниже) — и исправлен; поведение не менялось.
* Ревью-замечания (не дефекты, код не менялся): (1) `GET /cart` берёт
  `users FOR UPDATE`, хотя для согласованности ответа это не обязательно —
  убрать строку `_lock_user` в `get_cart`, если не нужна «свежесть после
  чужих операций»; (2) если пользователя удалить ровно между проверкой
  токена и сервисом, `_lock_user` даст 400 `ShopError`, а не 401 — окно
  недостижимо через API (401 отдаёт зависимость), но формально разные
  коды; (3) `IntegrityError` в `update_cart_item` пробрасывается как 500 —
  схема его не допускает (`ge=1`), но при будущем изменении CHECK стоит
  отдать 409.

## Чего ещё нет

* Корзина готова целиком и **принята ревью** (часть 6): добавление
  (`POST /cart/items`), чтение (`GET /cart`), абсолютное количество
  (`PATCH /cart/items/{item_id}`), удаление (`DELETE /cart/items/{item_id}`).
  Checkout (`POST /orders`), смена статуса заказа (`403` неадминистратору)
  и списание остатка не начаты.
* Запись каталога подключена и защищена; `require_admin` работает.
  Оформления заказа и связи товара с заказом через API ещё нет.
* Публичного маршрута назначения администратора нет: роль меняется только
  прямым UPDATE в БД (для приёмки админ создаётся так же).
* **Отложенное требование M2 закрыто** матрицей записи на результата M3
  (часть 9). Осталось на будущие модули: остальные операции корзины и
  оформление заказа.
* Swagger не умеет логиниться JSON-формой: `HTTPBearer` — это только поле
  для вставки токена. Порядок: вызвать `/auth/login`, затем Authorize.
* Постоянное время ответа входа не гарантируется: dummy_hash выравнивает
  только стоимость bcrypt, а сеть, БД и загрузка машины вносят свой вклад.
  Это записано и в docstring сервиса.
* Мелочь на будущее: пустые query-параметры (`?limit=`, `?offset=`,
  `?brand_id=`) дают 422, потому что пустая строка не парсится в int;
  `?search=` при этом не сужает выдачу. Поведение зафиксировано, но в
  задании не оговорено.
* Фильтра по цене «в диапазоне» через отдельный параметр нет — есть два
  (`min_price`/`max_price`), как в задании.
* Постраничности с `total` нет намеренно: массивы без обёртки. Состав
  страниц не гарантирован при параллельных вставках/удалениях
  (keyset-пагинация — отдельная задача).
* Корзины (кроме добавления), оформления заказа — нет. Регистрация и JWT
  сделаны в M3.
* Каскадного удаления и обработчиков покупки нет намеренно.
* Не проверялись: конкурентное списание остатка, гонка при оформлении,
  `ON DELETE` на практике, `alembic downgrade` в последнем прогоне.
* В этом шаге НЕ перезапускались наборы M3 (`_write_matrix_probe.py`,
  `_m2_contract_review.py`, `_m3_review_probe.py`) и `_auth_*`: изменения
  M4 к ним не относятся (правки ровно в `app/app_factory.py` — новый
  роутер и обработчик ShopError, `app/routers/auth.py` — новая зависимость
  `get_current_user_id`) плюс новые файлы. Регрессия проверена точечно:
  чтения каталога, `/health`, 401 на записи каталога без токена, 405 в
  контрольной точке M2 (корзины там нет — 404).

## Входные условия M5 (checkout) — зафиксированы ревью M4

Checkout **не начат**. Ниже — что уже гарантировано и что нужно решить, чтобы
не переизобретать и не ломать принятые правила.

**Что готово и на что опираться**

* Четыре маршрута корзины приняты ревью (часть 6); `_m4_review_probe.py`
  зелёный, остальные 7 наборов M4 тоже.
* Модели `Order` и `OrderItem` **уже есть** с M1 и менять схему не нужно:
  `orders.status` c `CHECK status IN ('created','processing','completed')`,
  `orders.total` `CHECK total >= 0`, `order_items.quantity > 0`,
  `order_items.price_at_purchase > 0`, 7 FK. Миграция для M5 не требуется,
  если не понадобится новое поле.
* Снимок цены: `order_items.price_at_purchase` — цена ОДНОЙ пары на момент
  покупки; `orders.total` — снимок суммы. `updated_at`/`created_at` у заказа
  заполняет сервер (`func.now()`).
* Общая инфраструктура: `ShopError`/`ShopNotFound`/`ShopConflict` и
  обработчик в `app_factory.py`; `shop_service.new_session()`; `user.id` из
  `get_current_user_id`; плюс `_lock_user`, `_lock_product_size`,
  `_get_product`, `_item_read`, `_subtotal` — их можно переиспользовать.

**Инварианты, которые checkout обязан сохранить**

1. Свежая сессия на операцию; `user.id` (не ORM-объект) из токена; commit и
   rollback — внутри сервиса; `begin_nested` не использовать.
2. Порядок захвата: `users FOR UPDATE` → `product_sizes` **по возрастанию
   id** → `products FOR SHARE` **по возрастанию id**. Он должен совпадать с
   корзиной, иначе возможен deadlock.
3. Остаток списывается ТОЛЬКО здесь и ровно один раз:
   `UPDATE product_sizes SET stock = stock - :qty WHERE id = :id AND stock >= :qty`;
   `rowcount == 0` → 409 (данные не изменены, полный откат).
4. Корзина не резервирует остаток (проверено в M4): между `PATCH` и
   checkout остаток мог измениться, поэтому проверка — под замком и в этой
   же транзакции, а не «по данным корзины».
5. Цена фиксируется снимком в момент checkout (`price_at_purchase`), сумма —
   `orders.total`; после этого изменение каталога на заказ не влияет.
6. Корзина и заказ меняются в ОДНОЙ транзакции: либо создан заказ и
   списан остаток, и корзина очищена, либо не изменено ничего.
7. Повторный checkout при неизвестном исходе соединения **не повторяется
   автоматически** (нет retry-цикла): клиент повторяет сам и получает
   осмысленный ответ (409/409/404 или новый заказ — решение ниже).

**Решения, которые нужно принять до кода (открытые вопросы)**

* Пустая корзина: `400` (нечего оформлять), `409` (конфликт состояния) или
  `404`? Сейчас в проекте «нет данных» — это `404`, «нельзя» — `409`;
  предлагается **409** `Cart is empty` (состояние не позволяет операцию).
* Что делать, если в корзине количество больше остатка (могло случиться):
  отказ 409 целиком или списать сколько есть? По контракту — **409 и
  ничего не менять** (частичный заказ не согласован с пользователем).
* Порядок списания при нескольких позициях: по возрастанию
  `product_sizes.id` (чтобы совпадало с порядком блокировок).
* Ответ: `201` + `OrderRead` (id, status, total, items) или `200`?
  Предлагается `201`.
* Кто и как меняет статус: отдельный маршрут `PATCH /orders/{id}/status`
  под `require_admin`; неадминистратор → `403`; допустимые значения — из
  `CHECK`; чужой/несуществующий заказ → `404`; пользователь свой статус
  менять не может.

**Что понадобится для проверки M5**

* Seed: size101 stock3/7990.00, size102 stock0, size201 stock5/5990.00,
  size301 stock2/9990.00.
* Сценарии: успешный checkout одной/двух позиций; 409 при нехватке;
  пустая корзина; повторный checkout; конкурентный checkout двух
  покупателей последней пары (настоящий параллельный тест по правилу 19);
  admin-смена статуса (403/404/422/200).
* Наблюдения после каждого отказа: `orders=0`, `order_items=0`,
  `cart_items` не изменились, `stock` не изменился.



## Правила написания приёмочных скриптов (грабли, на которые уже наступали)

Короткий список, чтобы в новой сессии не повторять одни и те же ошибки
вспомогательных скриптов (`_*_probe.py`). Код приложения тут ни при чём —
это про инструменты проверки.

1. **`urllib` шлёт GET, если `data=None`**, даже когда в `Request` передан
   `method="POST"`. Поэтому тело задаётся всегда (`b""` для запросов без
   тела), иначе POST/DELETE молча уходят как GET и приходит 405 — выглядит
   как «дефект маршрута», хотя дефект в пробе.
2. **Аргументы HTTP-хелпера передавать только именованными.** Один раз
   `"POST"` попал в позиционный параметр `token` и ушёл как
   `Authorization: Bearer POST` при методе GET.
3. **Читать атрибуты ORM-объекта внутри сессии**: после выхода из
   `with Session(...)` объект отсоединён, и `obj.id` даёт
   `DetachedInstanceError`.
4. **Порядок удаления фикстур**: сначала `order_items`/`orders`/`cart_items`,
   затем `product_sizes`, только потом `products` (иначе FK не даст).
5. **`DROP DATABASE` не выполняется в одной транзакции с другими
   операторами** — по одному `-c` на базу.
6. **Не полагаться на seed как на фикстуру**: свои товары/пользователи
   создавать в самой пробе, с фиксированными id в стороне от 1..30
   (использованы 9001/9002, 9101/9102), и делать уборку в начале прогона,
   а не только в конце — тогда повторный запуск не ломается.
7. **Снимок данных делать через `to_jsonb(t)::text`**, а не `UNION` с
   `::text` по колонкам: типы колонок не согласуются, и запрос падает.
8. **Проверять неизменность данных только у отказов.** Для успешных
   операций (201/200/204) ожидается изменение — сравнение «до == после»
   там неверно по смыслу.
9. **Печатать журнал фактически выполненных запросов** (метод, путь, какой
   заголовок отправлен, код ответа). Это отделяет настоящие HTTP-запросы от
   «демонстрационной матрицы» и ускоряет разбор.
10. **Каждая приёмка — своя временная БД** (`_review_m01_probe.py --phase
    setup`), потому что пробы пишут и удаляют данные и портят ожидания
    друг друга. Своя БД на пробу, потом `--phase teardown`.
11. **Порт брать не 8000**: там работает приложение ученика с `--reload`.
    Приёмки идут на 8001 (`store_main`) и 8002 (`catalog_main`).
12. **Перед прогоном убедиться, что на порту нет осиротевшего процесса от
    прошлой сессии**: старый сервер держит порт и старую БД, а новый не
    поднимается — в итоге проверки идут не против того кода, который
    собран сейчас.
13. **URL временной БД задавать в окружении ДО первого импорта `app.*`**:
    `alembic upgrade` внутри прогона импортирует `alembic/env.py`, а тот —
    `app.models` и `app.core.config`, поэтому Engine и `SessionLocal` в
    `app/database.py` строятся на уровне модуля. Если выставить
    `DATABASE_URL` после этого, сервисы останутся подключены к рабочей БД —
    проба будет писать в неё, а свои проверки читать из временной (на эти
    грабли уже наступали: в рабочей БД появились лишние строки
    `cart_items`). Признак — сервис отвечает, а в своей БД строк нет.
    Поэтому в `_cart_service_probe.py` переменная задаётся в начале модуля,
    а в `_cart_items_acceptance.ps1` есть отдельная проверка
    «рабочая БД пуста перед вызовами».
14. **В PowerShell 5.1 тело JSON не передавать аргументом `curl.exe`**:
    кавычки и фигурные скобки переразбираются, тело приходит битым и
    FastAPI отвечает `422 json_invalid`. Тело писать в файл и передавать
    `--data-binary @file`. Скрипты `.ps1` держать ASCII: кириллица без BOM
    ломает парсер (проверено на `_cart_items_acceptance.ps1` и повторно на
    `_cart_get_acceptance.ps1`).
15. **Питоновский код не держать в here-string внутри `.ps1`**: в
    PowerShell 5.1 такой блок вместе с кириллицей в том же файле ломает
    разбор (`Missing closing '}'`, «`from` keyword is not supported»), а
    передача многострочного кода через `-c` вдобавок превращает CRLF в
    `\r` и валит Python. Фикстуры вынесены в отдельные `.py`
    (`_cart_get_fixtures.py`), а `.ps1` вызывает их с явным URL аргументом.
16. **`docker compose exec` из песочницы агента недоступен** (named pipe
    демона закрыт в режиме `workspace-write`): для служебных
    CREATE/DROP DATABASE HTTP-приёмка использует `_pg_admin.py` (psycopg
    напрямую, реквизиты из `.env`), а не `psql` внутри контейнера.
17. **Не утверждать «рабочая БД пуста»**. Ученик работает в своём
    приложении на 8000 против `sneaker_shop`, поэтому в ней законно
    появляются его пользователи и строки корзины. Проверка изоляции должна
    быть привязана к ДАННЫМ ПРОГОНА: адрес с случайным суффиксом не должен
    появляться в рабочей БД (`_cart_get_fixtures.py has-email`, либо запрос
    по email внутри пробы). Такая проверка ловит настоящий дефект («сервер
    писал не в свою БД») и не ломается от действий ученика. На эти грабли
    уже наступали: приёмочная проверка «cart_items=0» упала из-за его
    собственного теста.
18. **Изоляцию и сценарии с ролями писать на Python, а не на PowerShell**:
    `_cart_isolation_probe.py` сам поднимает uvicorn (stdio в файл, не в
    pipe), сам создаёт и удаляет временную БД и не зависит от капризов
    PS 5.1 с кодировкой и here-string. HTTP — через `urllib`, тело всегда
    передаётся (`b""`), токены не печатаются.
19. **Два последовательных запроса — это НЕ конкурентный тест.** Второй POST
    стартует после COMMIT первого и просто видит готовую строку: он
    проверяет дедупликацию, но ничего не говорит о гонке. Настоящий тест
    требует двух соединений и детерминированного порядка: одно держит
    блокировку, второе ждёт, ожидание подтверждается через
    `pg_stat_activity` (`wait_event_type='Lock'`) или `pg_locks`
    (`NOT granted`), затем первое коммитит. Только после этого видно, что
    второй читает СВЕЖИЕ данные (см. часть 4 в `_cart_duplicate_probe.py`).
20. **В пробе не ждать блокировку без предохранителя.** Первый запуск
    `_cart_duplicate_probe.py` завис навсегда: main-поток ждал `UPDATE` B,
    а B ждал коммит A, который должен был случиться в том же main-потоке
    (самодельный deadlock), и `statement_timeout` не сработал, потому что
    проба строила Engine сама, без `options`. Лечится двумя вещами:
    блокирующую запись делать в отдельном потоке и задавать
    `connect_args={"options": "-c statement_timeout=8000 -c lock_timeout=8000"}`
    у всех Engine, которые создаёт проба.
