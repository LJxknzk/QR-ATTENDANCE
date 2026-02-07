import sys
import os
from pathlib import Path

# Ensure project root is on sys.path so `from app import app, db` works
project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

try:
    from app import app, db

    with app.app_context():
        db.create_all()
        print('Database tables created or already exist')
except Exception as e:
    print(f'init_db.py: Could not initialize database (this is OK during build): {e}')
    sys.exit(0)  # Exit cleanly so the build doesn't fail
