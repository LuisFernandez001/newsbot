# Newsbot (HCLS Weekly Digest)

Automated Healthcare & Life Sciences news workflow:
- Collects daily RSS articles.
- Builds a weekly HTML digest with AI summarization.
- Sends weekly emails to subscribers.
- Provides API endpoints for subscribe/unsubscribe + admin subscriber management.

## Repository Contents

- `rss_daily_summary.py` — main daily/weekly pipeline and email sender.
- `news_api.py` — Flask API for subscribe/unsubscribe and admin list management.
- `admin_app.py` — Flask admin UI for operations and subscriber controls.
- `test_email_logic.py` — unit test for email-safe HTML formatting.
- `requirements.txt` — Python dependencies.

## Requirements

- Python 3.10+
- pip
- SMTP credentials (for email sending)
- OpenAI API key

Install dependencies:

```bash
pip install -r requirements.txt
```

## Environment Variables

Create a `.env` file in the project root (or use `/opt/newsbot/.env` in deployment):

```env
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4.1-mini
RSS_URL=https://www.fiercehealthcare.com/rss/xml
SITE_NAME=Healthcare & Life Sciences

BASE_URL=http://newsweeklydigest.duckdns.org
API_BASE=/newsapi

DATA_DIR=/opt/newsbot/data
OUT_DIR=/opt/newsbot/out

SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-user@example.com
SMTP_PASS=your-app-password
EMAIL_FROM=your-user@example.com
DEFAULT_SUBSCRIBERS=you@example.com

SUB_SECRET=change-me
ADMIN_TOKEN=subslist
```

## Run Jobs

Daily collection:

```bash
python rss_daily_summary.py daily
```

Weekly digest generation + email send:

```bash
python rss_daily_summary.py weekly
```

Send latest weekly digest as test:

```bash
python rss_daily_summary.py send-test
```

## API (Flask)

Start API:

```bash
python news_api.py
```

Key endpoints:

- `GET /health`
- `POST /subscribe` with JSON body: `{ "email": "user@example.com" }`
- `GET /unsubscribe?email=...&token=...`
- `GET /list-subscribers?token=<ADMIN_TOKEN>`
- `GET /list-subscribers?token=<ADMIN_TOKEN>&add=email@example.com`
- `GET /list-subscribers?token=<ADMIN_TOKEN>&remove=email@example.com`

## Admin App

Start admin dashboard:

```bash
python admin_app.py
```

The admin app is intended for:
- running daily/weekly jobs manually,
- reviewing activity/log data,
- managing subscribers and customer list entries.

## Testing

Run the included unit test:

```bash
python test_email_logic.py
```

## Output/Data Paths

- Digest output HTML: `OUT_DIR` (default `/opt/newsbot/out`)
- Article DB: `DATA_DIR/news_log.json`
- Subscriber DB: `DATA_DIR/subscribers.json`
- Email send log: `DATA_DIR/weekly_email_log.json`

## GitHub Push (if needed)

If your local repo has no remote:

```bash
git remote add origin https://github.com/LuisFernandez001/newsbot.git
git push -u origin <branch>
```
