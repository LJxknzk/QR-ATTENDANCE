"""
Try to run migrations via Flask-Migrate/Alembic if available; otherwise fall back to create_all().
This is safe to call on deploy.
"""
import sys
from pathlib import Path
project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app import app, db

with app.app_context():
    try:
        # Try to run alembic upgrade through flask_migrate
        from flask_migrate import upgrade
        print("Running Flask-Migrate upgrade...")
        upgrade()
        print("Migrations applied (if any)")
    except Exception as e:
        print(f"Flask-Migrate upgrade failed or not available: {e}")
        print("Falling back to db.create_all()")
        db.create_all()
        print("Database tables created (fallback)")