web: gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --forwarded-allow-ips="*"
# Release command: run DB initialization on platforms that honor a release phase
release: python scripts/init_db.py
