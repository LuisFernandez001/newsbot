"""
rss_daily_summary.py
HCLS weekly digest builder with subscriptions + Monday email + Admin Dashboard.

USAGE:
  python rss_daily_summary.py daily      # collect up to 10 healthcare-tech items today
  python rss_daily_summary.py weekly     # generate weekly HTML + email subscribers
  python rss_daily_summary.py send-test [email]   # send test digest
  python rss_daily_summary.py runserver  # run admin dashboard
"""

import os, re, sys, json, datetime as dt, html, smtplib, shutil, hmac, hashlib, urllib.parse, pathlib, requests, feedparser
from datetime import datetime
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from bs4 import BeautifulSoup
from flask import Flask, request, render_template_string, redirect, url_for, session

# ---------------- LOAD ENV ----------------
dotenv_path = pathlib.Path("/opt/newsbot/.env")
if dotenv_path.exists():
    for line in dotenv_path.read_text().splitlines():
        if not line.strip() or line.strip().startswith("#"): 
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

# ---------------- CONFIG ----------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
RSS_URL = os.getenv("RSS_URL","https://www.fiercehealthcare.com/rss/xml")
SITE_NAME = os.getenv("SITE_NAME", "Healthcare & Life Sciences")

BASE_URL = os.getenv("BASE_URL", "http://newsweeklydigest.duckdns.org")
NEWS_PATH = "/news"
API_BASE = os.getenv("API_BASE", "/newsapi")

DATA_DIR = Path("/opt/newsbot/data")
OUT_DIR = Path("/opt/newsbot/out")
SUBSCRIBERS_FILE = DATA_DIR / "subscribers.json"

MAX_ARTICLES = 10
CR_TZ = dt.timezone(dt.timedelta(hours=-6))
OPENAI_BASE = "https://api.openai.com/v1"

DATA_FILE = DATA_DIR / "news_log.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER)
DEFAULT_SUBSCRIBERS = os.getenv("DEFAULT_SUBSCRIBERS", "")

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

# ---------------- SUBSCRIBERS ----------------
def load_subscribers_dict():
    """Load subscribers as dict with last_sent field (backward compatible)."""
    if not SUBSCRIBERS_FILE.exists():
        return {}
    with open(SUBSCRIBERS_FILE, "r", encoding="utf-8") as f:
        subs = json.load(f)
    if isinstance(subs, list):
        subs = {email: {"last_sent": None} for email in subs}
        save_json(SUBSCRIBERS_FILE, subs)
    return subs

def save_subscribers_dict(subs):
    save_json(SUBSCRIBERS_FILE, subs)

def ensure_subscribers():
    subs = load_subscribers_dict()
    if not subs and DEFAULT_SUBSCRIBERS:
        for e in [x.strip() for x in DEFAULT_SUBSCRIBERS.split(",") if x.strip()]:
            subs[e] = {"last_sent": None}
        save_subscribers_dict(subs)
    return list(subs.keys())

def update_last_sent(email):
    subs = load_subscribers_dict()
    if email not in subs:
        subs[email] = {"last_sent": None}
    subs[email]["last_sent"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    save_subscribers_dict(subs)

# ---------------- OPENAI SUMMARY ----------------
def openai_summarize(prompt_text):
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
    text = re.sub(r"^```(?:html)?", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"```$", "", text).strip()
    try:
        soup = BeautifulSoup(text, "html.parser")
        text = str(soup)
    except Exception:
        pass
    return text

# ---------------- RSS FETCH ----------------
def fetch_rss_items(rss_url):
    headers = {"User-Agent": "Mozilla/5.0 (Linux; HCLSNewsBot/1.0; +https://example.org)"}
    resp = requests.get(rss_url, headers=headers, timeout=30)
    resp.raise_for_status()
    feed = feedparser.parse(resp.text)
    entries = feed.entries
    KEYWORDS = ["health","medical","pharma","biotech","life science","hospital","digital health","ai","data","medtech","research","policy"]
    items = []
    for e in entries:
        title = e.get("title","").strip()
        link = e.get("link","")
        desc = e.get("summary","")
        snippet = re.sub(r"<[^>]+>"," ",desc)
        snippet = re.sub(r"\s+"," ",snippet).strip()
        if any(k in title.lower() or k in desc.lower() for k in KEYWORDS):
            items.append({"date":today_str(),"title":title,"url":link,"snippet":snippet})
    return items[:MAX_ARTICLES]

# ---------------- DAILY & WEEKLY ----------------
def load_db(): return load_json(DATA_FILE, [])
def save_db(d): save_json(DATA_FILE, d)

def daily_collect():
    db = load_db()
    today_entries = [d for d in db if d["date"] == today_str()]
    if len(today_entries) >= MAX_ARTICLES:
        print("Already collected today.")
        return
    new_items = fetch_rss_items(RSS_URL)
    existing_urls = {d["url"] for d in db}
    added = [i for i in new_items if i["url"] not in existing_urls]
    if not added:
        print("No new items.")
        return
    db.extend(added)
    save_db(db)
    print(f"✅ Added {len(added)} items for {today_str()}.")

def wrap_html(content_fragment, start_date, end_date):
    period = f"{start_date} – {end_date}"
    return f"""<!doctype html><html><head><meta charset='utf-8'>
<title>HCLS — Weekly Summary ({html.escape(period)})</title>
<style>body{{font-family:Arial,Helvetica,sans-serif;background:#f9fafb;color:#111827;padding:2rem;}}</style>
</head><body><h1>HCLS — Weekly Summary ({period})</h1>{content_fragment}</body></html>"""

def weekly_digest():
    db = load_db()
    if not db: return
    now = dt.datetime.now(CR_TZ)
    week_ago = now - dt.timedelta(days=7)
    start_date, end_date = week_ago.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d")
    week_items = [d for d in db if start_date <= d["date"] <= end_date]
    if not week_items: return
    week_items.sort(key=lambda x: x["date"])
    summaries = "\n".join(f"{i['date']}: {i['title']} — {i['snippet']} ({i['url']})" for i in week_items)
    prompt = ("Create a weekly digest for HCLS leaders. Begin with <h2>What matters</h2>..."
              f"\nArticles:\n{summaries}")
    html_fragment = openai_summarize(prompt)
    final_html = wrap_html(html_fragment, start_date, end_date)
    month_dir = OUT_DIR / now.strftime("%Y-%m")
    month_dir.mkdir(parents=True, exist_ok=True)
    out_path = month_dir / f"weekly-{end_date}.html"
    out_path.write_text(final_html, encoding="utf-8")
    print(f"✅ Wrote {out_path}")
    send_weekly_email_if_monday(out_path, f"{start_date} – {end_date}")

# ---------------- EMAIL ----------------
def make_token(email): 
    secret = os.getenv("SUB_SECRET","change-me")
    return hmac.new(secret.encode(), email.lower().encode(), hashlib.sha256).hexdigest()

def make_email_safe_fragment(html_doc):
    try: soup = BeautifulSoup(html_doc,"html.parser")
    except: return html_doc
    for el in soup.select("script,style,.bar,form"): el.decompose()
    return str(soup.body or soup)

def send_email_html(to_list, subject, html_body):
    if not SMTP_USER or not SMTP_PASS: return
    for addr in to_list:
        token = make_token(addr)
        unsubscribe = f"{BASE_URL}/unsubscribe?email={urllib.parse.quote(addr)}&token={token}"
        safe_body = make_email_safe_fragment(html_body)
        email_template = f"""<html><body style='font-family:Arial;background:#fff;'>
        <table align='center' width='90%' style='max-width:900px;border:1px solid #e5e7eb;padding:20px;'>
        <tr><td><h2>HCLS Weekly Summary</h2>{safe_body}
        <hr><p style='font-size:12px;color:#6b7280;text-align:center'>
        You’re receiving this because you subscribed.<br>
        <a href='{unsubscribe}' style='color:#2563eb;'>Unsubscribe</a></p></td></tr></table></body></html>"""
        msg = MIMEMultipart("alternative")
        msg["From"], msg["To"], msg["Subject"] = EMAIL_FROM, addr, subject
        msg.attach(MIMEText(email_template,"html","utf-8"))
        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
                s.starttls(); s.login(SMTP_USER, SMTP_PASS)
                s.sendmail(EMAIL_FROM,[addr],msg.as_string())
            print(f"✅ Sent email to {addr}")
            update_last_sent(addr)
        except Exception as e:
            print(f"❌ Failed {addr}: {e}")

def send_weekly_email_if_monday(path, period):
    subs = ensure_subscribers()
    if not subs: return
    html_body = path.read_text(encoding="utf-8")
    for addr in subs:
        send_email_html([addr], f"HCLS — Weekly Summary ({period})", html_body)

def test_send_email(target_email=None):
    subs = [target_email] if target_email else ["lfernand@akamai.com"]
    weekly_files = sorted(OUT_DIR.rglob("weekly-*.html"))
    if not weekly_files: 
        print("No weekly files found."); return
    latest = weekly_files[-1]
    html_body = latest.read_text(encoding="utf-8")
    m = re.search(r"weekly-(\d{4}-\d{2}-\d{2})", latest.name)
    period = f"Week ending {m.group(1)}" if m else today_str()
    print(f"🚀 Sending test to {subs}")
    send_email_html(subs, f"🔧 Test HCLS Weekly Email ({period})", html_body)

# ---------------- ADMIN DASHBOARD ----------------
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "supersecretkey")
ADMIN_USER = os.getenv("ADMIN_USER")
ADMIN_PASS = os.getenv("ADMIN_PASS")

@app.route("/admin/", methods=["GET","POST"])
def admin_page():
    if "logged_in" not in session:
        if request.method == "POST":
            if (request.form.get("username")==ADMIN_USER and request.form.get("password")==ADMIN_PASS):
                session["logged_in"]=True; return redirect(url_for("admin_page"))
            return render_template_string(LOGIN_TEMPLATE,error="Invalid credentials")
        return render_template_string(LOGIN_TEMPLATE)
    subs = load_subscribers_dict()
    if request.method=="POST":
        if "add_email" in request.form:
            new_email=request.form.get("add_email","").strip().lower()
            if new_email and new_email not in subs:
                subs[new_email]={"last_sent":None}; save_subscribers_dict(subs)
        elif "remove_email" in request.form:
            subs.pop(request.form.get("remove_email"),None); save_subscribers_dict(subs)
        return redirect(url_for("admin_page"))
    q = request.args.get("q","").lower()
    filtered = {k:v for k,v in subs.items() if q in k.lower()} if q else subs
    return render_template_string(ADMIN_TEMPLATE, subs=filtered, query=q)

@app.route("/logout")
def logout(): session.clear(); return redirect(url_for("admin_page"))

LOGIN_TEMPLATE = """
<html><body style="font-family:sans-serif;max-width:400px;margin:60px auto;">
<h2>🔒 Admin Login</h2>
{% if error %}<p style="color:red">{{error}}</p>{% endif %}
<form method="POST">
<label>User:</label><input name="username" required><br><br>
<label>Pass:</label><input type="password" name="password" required><br><br>
<button type="submit">Login</button>
</form></body></html>"""

ADMIN_TEMPLATE = """
<html><body style="font-family:sans-serif;max-width:800px;margin:40px auto;">
<h2>🧭 Subscriber Management</h2><a href="{{url_for('logout')}}">Logout</a>
<form method="GET" style="margin-top:10px;">
<input name="q" value="{{query}}" placeholder="Search email..." style="padding:6px;width:60%;">
<button type="submit">🔍 Search</button>
</form>
<form method="POST" style="margin-top:10px;">
<input name="add_email" placeholder="Add new subscriber" style="width:60%;padding:6px;">
<button type="submit">➕ Add</button>
</form>
<table border="1" cellpadding="6" cellspacing="0" style="margin-top:20px;border-collapse:collapse;width:100%;">
<tr style="background:#f3f4f6;"><th>Email</th><th>Last Sent</th><th>Actions</th></tr>
{% for email,info in subs.items() %}
<tr><td>{{email}}</td><td>{{info.get('last_sent','–')}}</td>
<td><form method="POST" style="display:inline;">
<input type="hidden" name="remove_email" value="{{email}}">
<button type="submit" style="color:red;">❌ Remove</button></form></td></tr>
{% endfor %}
</table></body></html>"""

# ---------------- MAIN ----------------
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python rss_daily_summary.py [daily|weekly|send-test|runserver]"); sys.exit()
    cmd = sys.argv[1].lower()
    if cmd=="daily": daily_collect()
    elif cmd=="weekly": weekly_digest()
    elif cmd=="send-test":
        email = sys.argv[2] if len(sys.argv)>2 else None
        test_send_email(email)
    elif cmd=="runserver":
        app.run(host="0.0.0.0", port=5000, debug=False)
