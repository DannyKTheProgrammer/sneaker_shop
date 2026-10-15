# Точка входа M3: чтение каталога, регистрация/вход и запись каталога
# администратором. Это результат M3.
#
# Запуск: python -m uvicorn app.store_main:app --port 8000
#
# Отличие от контрольной точки M2 (app/catalog_main.py) ровно одно: здесь
# подключён роутер записи app/routers/catalog_write.py, а все три его
# маршрута защищены require_admin.

from app.app_factory import build_app

app = build_app(include_writes=True)
