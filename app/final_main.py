# Финальная точка входа (M6): полный состав M3–M5 одной строкой.
#
# Запуск: python -m uvicorn app.final_main:app --port 8000
#
# Единственная точка входа: app_factory.build_app(include_writes=True) —
# каталог, регистрация/вход/JWT, запись каталога администратором,
# корзина, заказы и смена статуса.

from app.app_factory import build_app

app = build_app(include_writes=True)
