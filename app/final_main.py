# Точка входа: python -m uvicorn app.final_main:app --port 8000

from app.app_factory import build_app

app = build_app(include_writes=True)
