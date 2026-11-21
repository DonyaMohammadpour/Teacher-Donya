# Teacher Donya

A Persian (RTL) Flask application for managing private language classes, student accounts, lesson schedules, session credits, and payment status.

## Current features

- Teacher dashboard and individual student accounts
- Lesson calendar with Persian/Jalali dates
- Per-session and course-package billing
- Student payment requests with teacher approval/rejection
- Remaining session-credit calculations
- Student-facing notices for exhausted credits and outstanding balances
- Teacher list of students with outstanding balances

SMS integration is intentionally parked and is not part of the current setup instructions.

## Run locally (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python app.py
```

Then open `http://127.0.0.1:5000`.

For local testing, set a unique `SECRET_KEY` in `.env`. Do not use the example value for a public deployment.

## Database and privacy

- The app uses SQLite by default for local development.
- The local database, backups, and the entire `instance/` folder are excluded from Git by `.gitignore`.
- Never commit student names, phone numbers, usernames, password hashes, lesson records, payment records, database files, or real API keys.
- Keep a separate encrypted backup of the local database. Publishing this repository does not publish or transfer your local student data.

## Important before hosting this app publicly

This repository is a development version, not yet production-ready. Before connecting it to a public hosting service:

1. Disable or protect open teacher registration (the current `/register` route allows visitors to create teacher accounts).
2. Configure a strong secret key through the hosting provider's environment settings.
3. Use a persistent production database and configure automated backups.
4. Add CSRF protection, rate limiting, secure session-cookie settings, and HTTPS.
5. Use a production WSGI server; do not expose Flask's development server.
6. Test teacher/student data isolation and authentication flows before inviting students.

Do not upload your local database or `.env` file to GitHub. The public repository should contain source code only.
