"""
rss_hcls_weekly.py
HCLS weekly digest builder with subscriptions + Monday email.

USAGE:
  python rss_hcls_weekly.py daily     # collect up to 10 healthcare-tech items today
  python rss_hcls_weekly.py weekly    # generate weekly HTML + email subscribers
"""

import os, re, sys, json, datetime as dt, html, smtplib
from datetime import datetime
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests, feedparser
from bs4 import BeautifulSoup
import hmac, hashlib
import urllib.parse

# ---------------- CONFIG ----------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
RSS_URL = os.getenv("RSS_URL","https://www.fiercehealthcare.com/rss/xml")                       # e.g. https://example.com/rss.xml
SITE_NAME = os.getenv("SITE_NAME", "Healthcare & Life Sciences")

# Web URLs
BASE_URL = os.getenv("BASE_URL", "http://newsweeklydigest.duckdns.org")  # e.g. http://203.0.113.10 or https://news.yourdomain.com
NEWS_PATH = "/news"                                          # where Nginx serves your HTML
API_BASE = os.getenv("API_BASE", "/newsapi")                 # Nginx will proxy this to the local API

DATA_DIR = Path("/opt/newsbot/data")
OUT_DIR = Path("/opt/newsbot/out")
SUBSCRIBERS_FILE = DATA_DIR / "subscribers.json"

MAX_ARTICLES = 10                                # limit per day
CR_TZ = dt.timezone(dt.timedelta(hours=-6))      # Costa Rica
OPENAI_BASE = "https://api.openai.com/v1"

DATA_FILE = DATA_DIR / "news_log.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Email (use Gmail app password or any SMTP)
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "newsbot.digest@gmail.com")           # your SMTP username
SMTP_PASS = os.getenv("SMTP_PASS", "oazu qckz rlob excz")           # your SMTP password/app password
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER)
DEFAULT_SUBSCRIBERS = os.getenv("DEFAULT_SUBSCRIBERS", "fernandez.luisdiego@gmail.com")  # per your request

# ---------------- UTILITIES ----------------
def today_str():
    return dt.datetime.now(CR_TZ).strftime("%Y-%m-%d")

def load_json(path, default):
    if Path(path).exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def ensure_subscribers():
    subs = load_json(SUBSCRIBERS_FILE, [])
    # seed your email if list is empty and DEFAULT_SUBSCRIBERS is set
    if not subs:
        seeded = [e.strip() for e in DEFAULT_SUBSCRIBERS.split(",") if e.strip()]
        if seeded:
            save_json(SUBSCRIBERS_FILE, seeded)
            return seeded
    return subs

def openai_summarize(prompt_text):
    """Send prompt to OpenAI, return complete HTML."""
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    payload = {
        "model": OPENAI_MODEL,
        "input": [
            {"role": "system", "content":
                "You are an expert HCLS technology analyst. Output valid HTML only (no markdown)."},
            {"role": "user", "content": prompt_text}
        ],
        "max_output_tokens": 4000,
    }
    r = requests.post(f"{OPENAI_BASE}/responses", headers=headers, json=payload, timeout=120)
    r.raise_for_status()
    data = r.json()
    text = data["output"][0]["content"][0]["text"]
    # Strip accidental markdown fences
    text = re.sub(r"^```(?:html)?", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"```$", "", text).strip()
    # Repair HTML if needed
    try:
        soup = BeautifulSoup(text, "html.parser")
        text = str(soup)
    except Exception:
        pass
    return text

# ---------------- DAILY FETCH ----------------
def fetch_rss_items(rss_url):
    headers = {"User-Agent": "Mozilla/5.0 (Linux; HCLSNewsBot/1.0; +https://example.org)"}
    resp = requests.get(rss_url, headers=headers, timeout=30)
    resp.raise_for_status()
    feed = feedparser.parse(resp.text)
    entries = feed.entries

    KEYWORDS = [
        "health", "medical", "pharma", "biotech", "life science", "hospital",
        "digital health", "telemedicine", "ai", "machine learning", "data",
        "healthcare", "medtech", "drug", "research", "clinical", "technology"
    ]
    filtered = []
    for e in entries:
        title = (e.get("title") or "").lower()
        summary = (e.get("summary") or e.get("description") or "").lower()
        if any(k in title or k in summary for k in KEYWORDS):
            filtered.append(e)

    # build items, then cap at MAX_ARTICLES
    items = []
    for e in filtered:
        title = (e.get("title") or "Untitled").strip()
        link = e.get("link") or ""
        desc = e.get("summary") or e.get("description") or ""
        snippet = re.sub(r"<[^>]+>", " ", desc)
        snippet = re.sub(r"\s+", " ", snippet).strip()
        if not title or not link:
            continue
        items.append({
            "date": today_str(),
            "title": title,
            "url": link,
            "snippet": snippet
        })
    return items[:MAX_ARTICLES]

def load_db():
    return load_json(DATA_FILE, [])

def save_db(data):
    save_json(DATA_FILE, data)

def daily_collect():
    """Collect up to MAX_ARTICLES new healthcare-tech items for today."""
    db = load_db()

    # Prevent duplicate daily runs
    today_entries = [d for d in db if d["date"] == today_str()]
    if len(today_entries) >= MAX_ARTICLES:
        print(f"Already have {len(today_entries)} entries for today, skipping collection.")
        return

    new_items = fetch_rss_items(RSS_URL)
    existing_urls = {d["url"] for d in db}
    added = [i for i in new_items if i["url"] not in existing_urls]

    available_slots = MAX_ARTICLES - len(today_entries)
    if available_slots <= 0:
        print(f"Already reached {MAX_ARTICLES} articles for today — skipping.")
        return

    final_to_add = added[:available_slots]
    if not final_to_add:
        print("No new healthcare-related items to add today.")
        return

    db.extend(final_to_add)
    save_db(db)
    print(f"✅ Added {len(final_to_add)} new articles for {today_str()} (max {MAX_ARTICLES}).")

# ---------------- HTML ----------------
def wrap_html(content_fragment, start_date, end_date):
    period = f"{start_date} – {end_date}"
    # Buttons: Download PDF (uses window.print), Subscribe (POST to API)
    # NOTE: API endpoints proxied by Nginx at {API_BASE}
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>HCLS — Weekly Summary ({html.escape(period)})</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body {{ margin:0; padding:2rem; background:#0b0f1a; color:#e5e7eb;
          font:15px/1.6 system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial; }}
  .container {{ max-width:900px; margin:auto; background:#111827;
               border-radius:18px; padding:2rem; box-shadow:0 10px 24px rgba(0,0,0,.35); }}
  h1 {{ font-size:1.8rem; margin-bottom:0.5rem; }}
  h2 {{ color:#60a5fa; margin-top:1.5rem; }}
  .bar {{ display:flex; gap:.5rem; margin-bottom:1rem; flex-wrap:wrap; }}
  .btn {{ background:#1f2937; border:1px solid #374151; color:#e5e7eb; padding:.45rem .8rem; border-radius:10px; cursor:pointer; }}
  .btn:hover {{ background:#374151; }}
  input[type=email] {{ padding:.45rem .6rem; border-radius:8px; border:1px solid #374151; background:#0b0f1a; color:#e5e7eb; }}
  .msg {{ color:#9ca3af; margin-left:.5rem; }}
  a {{ color:#60a5fa; text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
</style>
</head>
<body><div class="container">
  <div class="bar">
    <button class="btn" onclick="window.print()">Download as PDF</button>
    <form id="subForm" onsubmit="return subscribe(event)">
      <input type="email" id="email" placeholder="you@example.com" required>
      <button class="btn" type="submit">Subscribe for Monday email</button>
      <span id="msg" class="msg"></span>
    </form>
  </div>

  <h1>HCLS — Weekly Summary</h1>
  <div>Period: {html.escape(period)}</div>
  <div style="height:1px;background:#222;margin:1rem 0;"></div>

  {content_fragment}
</div>

<script>
async function subscribe(e) {{
  e.preventDefault();
  const email = document.getElementById('email').value.trim();
  const msg = document.getElementById('msg');
  msg.textContent = 'Subscribing...';
  try {{
    const res = await fetch('{API_BASE}/subscribe', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ email }})
    }});
    if (!res.ok) throw new Error('Request failed');
    const data = await res.json();
    msg.textContent = data.message || 'Subscribed!';
  }} catch (err) {{
    msg.textContent = 'Failed to subscribe.';
  }}
}}
</script>
</body>
</html>"""

# ---------------- WEEKLY DIGEST ----------------
def weekly_digest():
    db = load_db()
    if not db:
        print("No data found.")
        return

    now = dt.datetime.now(CR_TZ)
    week_ago = now - dt.timedelta(days=7)
    start_date = week_ago.strftime("%Y-%m-%d")
    end_date = now.strftime("%Y-%m-%d")

    week_items = [d for d in db if start_date <= d["date"] <= end_date]
    if not week_items:
        print("No items in last 7 days.")
        return

    # Sort chronologically
    week_items.sort(key=lambda x: x["date"])

    summaries = "\n".join(
        f"{i['date']}: {i['title']} — {i['snippet']} (URL: {i['url']})"
        for i in week_items
    )

    # Group by category sections (model infers categories)
    prompt = (
        "Create a weekly digest for the Healthcare & Life Sciences technology industry. "
        "Begin with <h2>What matters</h2> and 3–5 paragraphs with strategic takeaways for HCLS leaders. "
        "Then group the following articles by thematic category (e.g., AI in Healthcare, Telemedicine, "
        "Pharma & Research, Digital Health, Policy & Regulation). For each category, output an <h2>Category</h2> "
        "and a <ul> with <li><strong>Title</strong> — one-sentence summary with <a href>source link</a></li>. "
        "No 'Sources' list. Only HTML.\n\n"
        f"Articles:\n{summaries}"
    )

    html_fragment = openai_summarize(prompt)
    final_html = wrap_html(html_fragment, start_date, end_date)

    # Create a folder for the month (e.g., /opt/newsbot/out/2025-10/)
    month_dir = OUT_DIR / now.strftime("%Y-%m")
    month_dir.mkdir(parents=True, exist_ok=True)

    out_path = month_dir / f"weekly-{end_date}.html"

    out_path.write_text(final_html, encoding="utf-8")
    print(f"Wrote weekly digest: {out_path}")

    # Email subscribers every Monday at 07:00 (CR time) OR always if you prefer.
    send_weekly_email_if_monday(out_path, period=f"{start_date} – {end_date}")

    # Update index.html to latest
    set_latest_index()
    build_archive_index()
    set_latest_daily()

def set_latest_daily():
    """Find the newest daily report and update daily.html in OUT_DIR and web root."""
    files = list(OUT_DIR.rglob("daily-*.html"))
    if not files:
        print("No daily files found to link as daily.html.")
        return

    def extract_dt(path):
        """Extract date from daily-YYYY-MM-DD.html, fallback to mtime."""
        m = re.search(r"daily-(\d{4})-(\d{1,2})-(\d{1,2})", path.name)
        if m:
            y, mo, d = map(int, m.groups())
            try:
                return datetime(y, mo, d)
            except ValueError:
                pass
        return datetime.fromtimestamp(path.stat().st_mtime)

    latest = max(files, key=extract_dt)

    print("Detected daily files (oldest → newest):")
    for f in sorted(files, key=extract_dt):
        print(f"  {f} → {extract_dt(f)}")
    print(f"Chosen latest daily: {latest.name}")

    # Write latest as OUT_DIR/daily.html
    daily_path = OUT_DIR / "daily.html"
    daily_path.write_text(latest.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Updated daily.html → {latest.name}")

    # Copy to Nginx web root
    try:
        shutil.copy(str(daily_path), "/var/www/news/daily.html")
        print("Copied latest daily.html to /var/www/news/")
    except Exception as e:
        print(f"Warning: failed to copy daily.html to /var/www/news/: {e}")

def build_daily_log_page():
    """
    Generate /opt/newsbot/out/daily.html showing all daily collected articles,
    grouped by date (newest first).
    """
    data_file = DATA_DIR / "news_log.json"
    if not data_file.exists():
        print("No news_log.json found; skipping daily page.")
        return

    try:
        with open(data_file, "r", encoding="utf-8") as f:
            db = json.load(f)
    except Exception as e:
        print(f"Error loading {data_file}: {e}")
        return

    if not db:
        print("No data in news_log.json; skipping daily page.")
        return

    grouped = {}
    for item in db:
        d = item.get("date")
        grouped.setdefault(d, []).append(item)

    sorted_days = sorted(grouped.keys(), reverse=True)
    sections = []
    for d in sorted_days:
        articles = grouped[d]
        items_html = ""
        for a in articles:
            title = html.escape(a.get("title", "Untitled"))
            url = a.get("url", "#")
            summary = html.escape(a.get("summary", ""))
            category = html.escape(a.get("category", ""))
            items_html += f"""
            <div style="margin-bottom:10px;padding:10px;border-bottom:1px solid #e5e7eb;">
              <a href="{url}" target="_blank" style="font-weight:bold;color:#2563eb;text-decoration:none;">{title}</a><br>
              <span style="font-size:13px;color:#6b7280;">{category}</span>
              <p style="margin:6px 0 0 0;color:#111827;font-size:14px;line-height:1.5;">{summary}</p>
            </div>
            """
        section = f"""
        <section style="margin-bottom:30px;">
          <h2 style="font-size:18px;color:#1f2937;">{d}</h2>
          {items_html}
        </section>
        """
        sections.append(section)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>HCLS Daily Inputs</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body {{ font-family:Arial,Helvetica,sans-serif; background:#ffffff; color:#111827; padding:2rem; }}
  h1 {{ font-size:24px; color:#1f2937; margin:0 0 16px 0; }}
  a:hover {{ text-decoration:underline; }}
</style>
</head>
<body>
  <h1>Healthcare & Life Sciences — Daily Inputs</h1>
  {"".join(sections) if sections else "<p>No daily inputs yet.</p>"}
</body>
</html>
"""
    out_path = OUT_DIR / "daily.html"
    out_path.write_text(html_content, encoding="utf-8")
    print(f"✅ Daily log page updated: {out_path}")

def build_archive_index():
    """
    Build /opt/newsbot/out/archive.html listing months and weekly reports.
    Output path (public): http://<ip-or-domain>/news/archive.html
    """
    # Collect month folders like YYYY-MM
    months = sorted(
        [p for p in OUT_DIR.iterdir() if p.is_dir() and re.match(r"\d{4}-\d{2}$", p.name)],
        key=lambda p: p.name,
        reverse=True  # newest month first
    )

    # Build HTML sections
    month_sections = []
    for month_path in months:
        month_name = month_path.name  # e.g., 2025-10

        # All weekly files inside the month
        weekly_files = sorted(
            month_path.glob("weekly-*.html"),
            key=lambda p: p.name,
            reverse=True  # newest week first
        )

        if not weekly_files:
            continue

        # Per-week links with friendly label "Week ending YYYY-MM-DD"
        items_html = []
        for f in weekly_files:
            m = re.search(r"weekly-(\d{4}-\d{2}-\d{2})\.html", f.name)
            week_label = f"Week ending {m.group(1)}" if m else f.name
            # Public URL under /news/
            url = f"/news/{month_name}/{f.name}"
            items_html.append(f'<li><a href="{url}" style="color:#2563eb;text-decoration:none;">{week_label}</a></li>')

        section = f"""
        <section style="margin-bottom:20px;">
          <h2 style="margin:0 0 8px 0;font-size:18px;color:#1f2937;">{month_name}</h2>
          <ul style="list-style-type:none;padding-left:0;margin:0;">
            {''.join(items_html)}
          </ul>
        </section>
        """
        month_sections.append(section)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>HCLS Weekly Archive</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body {{ font-family:Arial,Helvetica,sans-serif; background:#ffffff; color:#111827; padding:2rem; }}
  h1 {{ font-size:24px; color:#1f2937; margin:0 0 16px 0; }}
  a:hover {{ text-decoration:underline; }}
</style>
</head>
<body>
  <h1>Healthcare & Life Sciences — Weekly Archive</h1>
  {"".join(month_sections) if month_sections else "<p>No reports yet.</p>"}
</body>
</html>
"""
    (OUT_DIR / "archive.html").write_text(html_content, encoding="utf-8")
    print("✅ Archive index updated at /news/archive.html")



def set_latest_index():
    # Recursively find all weekly HTML files inside OUT_DIR and subfolders
    files = list(OUT_DIR.rglob("weekly-*.html"))
    if not files:
        print("No weekly files found to link as index.")
        return

    def extract_dt(path):
        """Extract date from weekly-YYYY-MM-DD.html or fallback to mtime."""
        m = re.search(r"weekly-(\d{4})-(\d{1,2})-(\d{1,2})", path.name)
        if m:
            y, mo, d = map(int, m.groups())
            try:
                return datetime(y, mo, d)
            except ValueError:
                pass
        return datetime.fromtimestamp(path.stat().st_mtime)

    latest = max(files, key=extract_dt)

    # Debug info
    print("Detected weekly files (oldest → newest):")
    for f in sorted(files, key=extract_dt):
        print(f"  {f} → {extract_dt(f)}")
    print(f"Chosen latest: {latest.name}")

    # Write new index.html
    index_path = OUT_DIR / "index.html"
    index_path.write_text(latest.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Updated index.html → {latest.name}")

    # Copy to web root
    try:
        shutil.copy(str(index_path), "/var/www/news/index.html")
        print("Copied latest index.html to /var/www/news/")
    except Exception as e:
        print(f"Warning: failed to copy index.html to /var/www/news/: {e}")


# ---------------- EMAIL ----------------
def make_token(email: str) -> str:
    """
    Generate a secure HMAC token for an email address.
    Used for personalized unsubscribe links.
    """
    secret = os.getenv("SUB_SECRET", "change-me-please")
    return hmac.new(secret.encode(), email.lower().encode(), hashlib.sha256).hexdigest()
def make_email_safe_fragment(html_doc: str) -> str:
    """
    Extract a clean, email-friendly fragment:
    - grabs only the main content (.container or <body>)
    - removes <style>, <script>, .bar (print/subscribe) etc.
    - strips classes/ids/inline styles that force dark UI
    - applies minimal inline light styles to headings, links, text
    """
    try:
        soup = BeautifulSoup(html_doc, "html.parser")
    except Exception:
        # If parsing fails, return original
        return html_doc

    # 1) Pick main content
    root = soup.select_one("div.container") or soup.body or soup

    # 2) Remove scripts, styles, and the top control bar
    for el in root.select("script, style, .bar, form"):
        el.decompose()

    # 3) Strip attributes that might force dark theme
    for tag in root.find_all(True):
        if tag.name == "a":
            href = tag.get("href", "")
            tag.attrs = {}
            if href:
                tag["href"] = href
                tag["style"] = "color:#2563eb;text-decoration:none;"
        else:
            tag.attrs = {}

    # 4) Apply minimal inline light styles
    for h in root.find_all(["h1", "h2", "h3"]):
        if h.name == "h1":
            h["style"] = "margin:16px 0 10px 0;font-size:20px;color:#111827;"
        elif h.name == "h2":
            h["style"] = "margin:18px 0 10px 0;font-size:16px;color:#111827;"
        else:
            h["style"] = "margin:12px 0 8px 0;font-size:15px;color:#111827;"

    for p in root.find_all("p"):
        p["style"] = "margin:8px 0 12px 0;color:#111827;"

    for li in root.find_all("li"):
        li["style"] = "margin:6px 0;color:#111827;"

    for ul in root.find_all("ul"):
        ul["style"] = "padding-left:20px;margin:6px 0 12px 0;"

    # Return only the cleaned inner HTML
    return "".join(str(c) for c in root.children) or str(root)

def send_email_html(to_list, subject, html_body):
    """
    Send styled HTML digest emails that render cleanly in Gmail, Outlook, etc.
    Each recipient receives a personalized unsubscribe link.
    Uses a white, minimal design for consistent readability.
    """
    if not SMTP_USER or not SMTP_PASS or not EMAIL_FROM:
        print("SMTP creds not set; skipping email.")
        return
    if not to_list:
        print("No recipients; skipping email.")
        return

    for addr in to_list:
        # Generate personalized unsubscribe link
        token = make_token(addr)
        query = urllib.parse.urlencode({"email": addr, "token": token})
        unsubscribe_link = f"{BASE_URL}{API_BASE}/unsubscribe?{query}"

        # 🔧 Sanitize the weekly HTML for email (forces light theme)
        safe_body = make_email_safe_fragment(html_body)

        email_template = f"""\
    <!DOCTYPE html>
    <html>
      <head>
        <meta charset="UTF-8">
        <title>{html.escape(subject)}</title>
      </head>
      <body style="margin:0;padding:0;background-color:#ffffff;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background-color:#ffffff;">
          <tr>
            <td align="center" style="padding:30px 0;">
              <table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0"
                     style="background-color:#ffffff;border:1px solid #e5e7eb;border-radius:8px;
                            font-family:Arial,Helvetica,sans-serif;color:#333333;">
                <tr>
                  <td style="padding:25px 30px 15px 30px;text-align:center;">
                    <h1 style="margin:0;font-size:22px;color:#1f2937;">Healthcare & Life Sciences Weekly Summary</h1>
                  </td>
                </tr>
                <tr>
                  <td style="padding:10px 30px 25px 30px;font-size:14px;line-height:1.6;color:#111827;">
                    {safe_body}
                  </td>
                </tr>
                <tr>
                  <td style="padding:20px 30px;text-align:center;font-size:12px;line-height:1.5;
                             color:#6b7280;border-top:1px solid #e5e7eb;">
                    You are receiving this because you subscribed to the HCLS Weekly Digest.<br>
                    <a href="{unsubscribe_link}" style="color:#2563eb;text-decoration:none;">Unsubscribe</a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
      </body>
    </html>
"""

        msg = MIMEMultipart("alternative")
        msg["From"] = EMAIL_FROM
        msg["To"] = addr
        msg["Subject"] = subject

        body = MIMEText(email_template, "html", "utf-8")
        msg.attach(body)

        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
                s.starttls()
                s.login(SMTP_USER, SMTP_PASS)
                s.sendmail(EMAIL_FROM, [addr], msg.as_string())
            print(f"✅ Sent email to {addr}")
        except Exception as e:
            print(f"❌ SMTP send failed for {addr}: {e}")

from urllib.parse import urlencode

def add_unsubscribe_footer(html_body: str, email_addr: str) -> str:
    """
    Append a consistent unsubscribe footer to each HTML email.
    Includes a personalized unsubscribe link for the given address.
    """
    token = make_token(email_addr) if 'make_token' in globals() else ""
    query = urlencode({"email": email_addr, "token": token})
    unsubscribe_url = f"http://newsweeklydigest.duckdns.org/unsubscribe?{query}"

    footer_html = f"""
    <hr style="border:none;border-top:1px solid #ddd;margin:30px 0;">
    <div style="font-size:13px;color:#555;text-align:center;line-height:1.4;">
      You’re receiving this because you subscribed to the
      <b>HCLS Weekly Digest</b>.<br>
      <a href="{unsubscribe_url}"
         style="color:#2563eb;text-decoration:none;">Unsubscribe</a> anytime.
    </div>
    </body></html>
    """
    # ensure footer added only once
    if "</body>" in html_body:
        html_body = html_body.replace("</body>", footer_html)
    else:
        html_body += footer_html
    return html_body
def send_weekly_email_if_monday(weekly_path: Path, period: str):
    now = dt.datetime.now(CR_TZ)
    # if now.weekday() != 0 or now.hour < 7:  # Monday=0; ensure after 07:00
    #     # You can comment this out if you want to always send on weekly run
    #     print("Not Monday 07:00 CR; skipping email send.")
    #     return

    subs = ensure_subscribers()  # seed your email if empty
    if not subs:
        print("No subscribers.")
        return

    html_body = weekly_path.read_text(encoding="utf-8")
    # personalize unsubscribe footer per recipient (simple loop)
    for addr in subs:
        personalized = add_unsubscribe_footer(html_body, addr)
        send_email_html([addr], f"HCLS — Weekly Summary ({period})", personalized)

def test_send_email():
    """
    Send the latest weekly digest immediately to all current subscribers.
    Useful for testing email configuration and formatting.
    """
    subs = ensure_subscribers()
    if not subs:
        print("No subscribers found. Please subscribe first.")
        return

    weekly_files = sorted(OUT_DIR.glob("weekly-*.html"))
    if not weekly_files:
        print("No weekly digest files found. Run 'python rss_hcls_weekly.py weekly' first.")
        return

    latest = weekly_files[-1]
    html_body = latest.read_text(encoding="utf-8")

    # ✅ Define 'period' based on the filename or current date
    # Example: weekly-2025-10-07.html → "Week ending 2025-10-07"
    m = re.search(r"weekly-(\d{4}-\d{2}-\d{2})\.html", latest.name)
    period = f"Week ending {m.group(1)}" if m else dt.datetime.now(CR_TZ).strftime("Week ending %Y-%m-%d")

    print(f"Sending test digest '{latest.name}' ({period}) to {len(subs)} subscriber(s)...")

    # Send to all subscribers (each gets personalized unsubscribe link)
    send_email_html(subs, f"🔧 Test HCLS Weekly Email ({period})", html_body)

    print("✅ Test email(s) sent successfully.")


# ---------------- MAIN ----------------
if __name__ == "__main__":
    if not OPENAI_API_KEY or not RSS_URL:
        sys.exit("Missing OPENAI_API_KEY or RSS_URL.")
    if len(sys.argv) < 2:
        sys.exit("Usage: python rss_hcls_weekly.py [daily|weekly]")

    mode = sys.argv[1].lower()
    if mode == "daily":
        daily_collect()
    elif mode == "weekly":
        weekly_digest()
    elif mode == "send-test":
        test_send_email()
    else:
        sys.exit("Unknown mode: use 'daily', 'weekly', or 'send-test'.")
