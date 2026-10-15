# Контрольная точка M2: только чтение каталога, запись НЕ подключена.
#
# Запуск: python -m uvicorn app.catalog_main:app --port 8000
#
# Зачем оставлена: по OpenAPI и поведению видно, что было принято в M2.
# Здесь POST/PUT/DELETE каталога дают 405 (маршрутов нет), а GET остаются
# публичными. Это позволяет приёмке сравнивать этапы, не откатывая код.

from app.app_factory import build_app

app = build_app(include_writes=False)
