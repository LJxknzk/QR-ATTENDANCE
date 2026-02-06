Railway deployment and PostgreSQL setup

Overview
- This project is a Flask app (`app.py`) that supports either SQLite (local) or a production `DATABASE_URL` (Postgres).
- The code now normalizes `postgres://` → `postgresql://` for SQLAlchemy compatibility.
- The `Procfile` runs Gunicorn: `web: gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4`.

Railway steps (quick)
1. Create a new Railway project and link your GitHub repo or push code from your local machine.
2. Add a PostgreSQL plugin (Provision a new Postgres database). Railway will provide a `DATABASE_URL` env var automatically.
3. In Railway project settings > Variables, add:
   - `FLASK_SECRET_KEY` : set to a strong secret
   - `QR_SIGNING_SECRET` : optional, or defaults to `FLASK_SECRET_KEY`
   - `MAIL_USERNAME` / `MAIL_PASSWORD` : if you plan to send email notifications
   - `SESSION_COOKIE_SECURE` : `True`
   - `SESSION_COOKIE_SAMESITE` : `None`
4. Deploy the project (Railway uses the `Procfile` and `requirements.txt`).

Database initialization (no migrations)
- This app uses `db.create_all()` on startup (no Alembic). After Railway deploy, you can:
  - Use a one-off Railway shell to run a short Python snippet to create tables if needed:

```
from app import app, db
with app.app_context():
    db.create_all()
```

- Alternatively, during the first deploy the app will auto-create tables when SQLAlchemy hits the DB; ensure the app has necessary permissions.

Notes & best practices
- Ensure Railway's `DATABASE_URL` is present—it's used automatically. The code will normalize `postgres://` to `postgresql://`.
- Set `FLASK_SECRET_KEY` in Railway to a secure value.
- For production, Railway runs Gunicorn (Procfile); the `if __name__ == '__main__'` block is for local development only.
- Backups: enable automated backups for the Railway Postgres plugin to avoid data loss.

Desktop and mobile build notes
- Desktop (executable): The repo contains build artifacts and `build` helpers; use PyInstaller or the provided `build.bat`/`build.ps1` to create a distributable. Ensure the production server uses PostgreSQL; desktop builds can keep using local SQLite.
- Mobile (Capacitor): The `www/` folder contains the web build. To ship the mobile app, build the web assets, then follow the Capacitor docs to produce Android/iOS builds. For production, the mobile app should point to your Railway-hosted API endpoints and rely on the Postgres-backed server.

Useful commands (local)
- Create virtualenv and install deps:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

- Run locally with environment vars:

```powershell
$env:FLASK_SECRET_KEY = 'replace-with-secret'; $env:DATABASE_URL = 'postgresql://user:pass@host:5432/dbname'; python .\app.py
```

Questions or want me to add a GitHub Actions workflow to auto-deploy to Railway? Ask and I can add it.
