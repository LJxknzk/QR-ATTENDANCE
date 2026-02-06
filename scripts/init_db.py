import sys
from pathlib import Path

# Ensure project root is on sys.path so `from app import app, db` works
project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app import app, db

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        print('Database tables created or already exist')
