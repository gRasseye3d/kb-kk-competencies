import os
import sys

PROJECT_ROOT = "/opt/kb"
sys.path.insert(0, PROJECT_ROOT)

# DATABASE_URL must be supplied by the deployment environment; never embed credentials here.
if not os.environ.get("DATABASE_URL"):
    raise RuntimeError("DATABASE_URL is required")
os.environ.setdefault("STATIC_DIR", f"{PROJECT_ROOT}/static")

from app.wsgi import application  # noqa: E402
