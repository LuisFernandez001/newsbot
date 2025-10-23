import os, re, datetime as dt, html
from pathlib import Path
import requests
import feedparser
import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="urllib3")

# =====================
# CONFIG (via env vars)
# =====================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL   = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
RSS_URL        = os.getenv("RSS_URL","https://www.fiercehealthcare.com/rss/xml")  # e.g., https://example.com/rss.xml
SITE_NAME      = os.getenv("SITE_NAME", "Fierce Health Tech")
MAX_ARTICLES   = int(os.getenv("MAX_ARTICLES", "20"))
OUT_DIR        = Path(os.getenv("OUT_DIR", "./out"))
OUT_DIR.mkdir(parents=True, exist_ok=True)

OPENAI_BASE = "https://api.openai.com/v1"

# Costa Rica (no DST): UTC-6
CR_TZ = dt.timezone(dt.timedelta(hours=-6))

def today_datestr():
    return dt.datetime.now(CR_TZ).strftime("%Y-%m-%d")

def is_today(entry):
    """
    Returns True if entry's published date is 'today' in Costa Rica time.
    If no date found, we'll include it later if needed to fill up to MAX_ARTICLES.
    """
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            # Convert struct_time (assumed UTC) to datetime and then to CR time
            dt_utc = dt.datetime(*t[:6], tzinfo=dt.timezone.utc)
            local = dt_utc.astimezone(CR_TZ)
            now = dt.datetime.now(CR_TZ)
            return (local.date() == now.date())
    return None  # unknown

def fetch_rss_items(rss_url):
    feed = feedparser.parse(rss_url)
    entries = feed.entries or []
    # Prefer today's items first
    todays, undated, others = [], [], []
    for e in entries:
        flag = is_today(e)
        if flag is True:
            todays.append(e)
        elif flag is None:
            undated.append(e)
        else:
            others.append(e)
    ordered = todays + undated + others
    items = []
    for e in ordered[:MAX_ARTICLES]:
        title = (e.get("title") or "Untitled").strip()
        link  = e.get("link") or ""
        summary = (
            e.get("summary") or e.get("description") or ""
        )
        # Light cleanup of feed HTML
        summary_text = re.sub(r"<[^>]+>", " ", summary)
        summary_text = re.sub(r"\s+", " ", summary_text).strip()
        items.append({
            "title": title,
            "url": link,
            "snippet": summary_text[:1200]
        })
    return items

def summarize_to_html(items):
    """
    Ask OpenAI to return an HTML fragment (no <html> or <body>),
    grouped by theme, with bullets and source links preserved.
    """
    sys = (
        "You are a concise news analyst. Output an HTML fragment only "
        "(no <html>, no <body>). Requirements:\n"
        "- Group stories by theme using <h2>.\n"
        "- Use short <ul><li> bullets per story.\n"
        "- Each bullet should end with a source link as <a href=\"...\">Source</a>.\n"
        "- Close all tags properly. Keep it clean and minimal.\n"
        "- End with a <h2>What matters</h2> and 2–4 brief <p> paragraphs."
    )
    # Build compact list for the model
    lines = []
    for it in items:
        lines.append(f"- {it['title']} :: {it['snippet']} [URL: {it['url']}]")
    user = (
        "Summarize today's articles from the feed. Here are title, snippet, and URL lines:\n"
        + "\n".join(lines)
    )

    payload = {
        "model": OPENAI_MODEL,
        "input": [
            {"role": "system", "content": sys},
            {"role": "user", "content": user}
        ],
        "max_output_tokens": 1400,
    }
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    r = requests.post(f"{OPENAI_BASE}/responses", headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    return data["output"][0]["content"][0]["text"]

def wrap_html(content_fragment, items):
    today = today_datestr()
    # Basic, portable HTML
    sources_list = "\n".join(
        f'<li><a href="{html.escape(it["url"])}" target="_blank" rel="noopener">{html.escape(it["title"])}</a></li>'
        for it in items
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(SITE_NAME)} — Daily Summary ({today})</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  :root {{
    --bg:#0b0f1a; --panel:#111827; --ink:#e5e7eb; --muted:#9ca3af; --accent:#60a5fa;
  }}
  body {{
    margin:0; padding:2rem; background:var(--bg); color:var(--ink);
    font:15px/1.6 system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
  }}
  .container {{
    max-width: 920px; margin: 0 auto; background: var(--panel);
    border-radius: 18px; padding: 2rem; box-shadow: 0 10px 24px rgba(0,0,0,.35);
  }}
  h1 {{ font-size: 1.8rem; margin: 0 0 1rem; }}
  h2 {{ font-size: 1.25rem; margin: 1.5rem 0 .5rem; color: var(--accent); }}
  ul {{ padding-left: 1.1rem; }}
  a {{ color: var(--accent); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .meta {{ color: var(--muted); margin-bottom: 1rem; }}
  .sources {{ margin-top: 2rem; }}
  .divider {{ height:1px; background:#232a3b; margin:1.5rem 0; }}
</style>
</head>
<body>
  <div class="container">
    <h1>{html.escape(SITE_NAME)} — Daily Summary</h1>
    <div class="meta">Date: {today}</div>
    <div class="divider"></div>
    {content_fragment}
    <div class="sources">
      <h2>Sources</h2>
      <ul>
        {sources_list}
      </ul>
    </div>
  </div>
</body>
</html>
"""

def main():
    if not OPENAI_API_KEY:
        raise SystemExit("Set OPENAI_API_KEY.")
    if not RSS_URL:
        raise SystemExit("Set RSS_URL to a valid RSS/Atom feed.")
    items = fetch_rss_items(RSS_URL)
    if not items:
        raise SystemExit("No items found in RSS feed.")
    fragment = summarize_to_html(items)
    html_doc = wrap_html(fragment, items)
    out_path = OUT_DIR / f"{today_datestr()}-digest.html"
    out_path.write_text(html_doc, encoding="utf-8")
    print(f"Wrote {out_path}")

if __name__ == "__main__":
    main()
