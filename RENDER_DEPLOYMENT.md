# QR Attendance Render Deployment Guide

## Environment Variables
- `DATABASE_URL`: (required) PostgreSQL connection string. Provided by Render PostgreSQL add-on.
- `FLASK_SECRET_KEY`: (required) A strong random string for Flask session security.

## Procfile
Your `Procfile` should contain:
```
web: gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4
```

## Requirements
- All dependencies are listed in `requirements.txt` and `pyproject.toml`.
- Make sure `psycopg2-binary` is included for PostgreSQL support.

## Static Files
- Static HTML and assets are served by Flask using `send_file`.
- No changes needed for Render.

## Deployment Steps
1. Push your code to your connected GitHub repo.
2. Create a new Web Service on Render and connect your repo.
3. Add a PostgreSQL database in Render and copy the `DATABASE_URL`.
4. Set the following environment variables in the Render dashboard:
   - `DATABASE_URL` (from Render PostgreSQL)
   - `FLASK_SECRET_KEY` (generate a secure random string)
5. Deploy the service. Render will use the `Procfile` and install all dependencies.
6. Your app will be available at the Render-provided URL.

## Notes
- All data is stored in PostgreSQL and is persistent.
- If the service sleeps or restarts, your data will NOT be lost.
- Do NOT use SQLite in production on Render.

---
For any issues, check the Render logs and ensure your environment variables are set correctly.
