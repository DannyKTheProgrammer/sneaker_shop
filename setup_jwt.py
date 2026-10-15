"""Generate a local key without printing it or replacing an existing key."""
from pathlib import Path
import secrets
from dotenv import dotenv_values, set_key

path = Path(__file__).resolve().parent / ".env"
if not path.exists():
    raise SystemExit("Create .env from .env.example first")
if dotenv_values(path).get("JWT_SECRET"):
    print("JWT_SECRET already exists; unchanged.")
else:
    set_key(path, "JWT_SECRET", secrets.token_urlsafe(48))
    print("JWT_SECRET saved to local .env; value not printed.")
