# Финальная точка входа (M6): полный состав M3–M5 одной строкой.
#
# Запуск: python -m uvicorn app.final_main:app --port 8000
#
# Это та же сборка, что и app/store_main.py (app_factory.build_app с
# include_writes=True): каталог, регистрация/вход/JWT, запись каталога
# администратором, корзина, заказы и смена статуса. Отдельный модуль нужен
# только для того, чтобы финальную команду запуска можно было назвать по
# карте задания; сохранённые точки (main, catalog_main, store_main)
# оставлены для обучения и не меняются.

from app.app_factory import build_app

app = build_app(include_writes=True)
