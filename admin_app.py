"""
admin_app.py — Flask Admin Dashboard for Newsbot
Accessible at: http://newsweeklydigest.duckdns.org/admin/
Integrates with /newsapi/list-subscribers and adds controls for cron + job execution.
"""

from flask import Flask, request, Response, render_template_string, jsonify
import os, requests, subprocess, json
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
import pathlib

# ---------------- CONFIG ----------------
# Load local .env if present (similar to rss_daily_summary.py)
local_dotenv = pathlib.Path(".env")
if local_dotenv.exists():
    with open(local_dotenv, "r") as f:
        for line in f:
            if line.strip() and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "changeme")

# Use Nginx proxy for newsapi so we don't depend on internal ports here
NEWSAPI_LIST_URL = "http://127.0.0.1/newsapi/list-subscribers?token=subslist"

NEWSBOT_PATH = "/opt/newsbot"
VENV_PY = "/opt/newsbot/venv/bin/python"  # use venv runner for jobs
LOG_FILE = "/opt/newsbot/run.log"

# Allow DATA_DIR override for local testing
DATA_DIR = Path(os.getenv("DATA_DIR", "/opt/newsbot/data"))
DATA_FILE = DATA_DIR / "news_log.json"
SUBSCRIBERS_FILE = DATA_DIR / "subscribers.json"
CUSTOMERS_FILE = DATA_DIR / "customers.txt"

app = Flask(__name__)
# allow enumerate in Jinja
app.jinja_env.globals['enumerate'] = enumerate


# ---------------- AUTH ----------------
def check_auth(username, password):
    return username == ADMIN_USER and password == ADMIN_PASS

def authenticate():
    return Response(
        "Authentication required", 401,
        {"WWW-Authenticate": 'Basic realm="Login Required"'}
    )

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated


# ---------------- UTILITIES ----------------
def load_subscribers():
    """Load subscribers list from JSON file (UI display)."""
    try:
        if not SUBSCRIBERS_FILE.exists():
            return []
        with open(SUBSCRIBERS_FILE, "r", encoding="utf-8") as f:
            subs = json.load(f)
        subs = sorted(set(s.strip().lower() for s in subs if s and s.strip()))
        return subs
    except Exception as e:
        print(f"Error loading subscribers: {e}")
        return []

def load_customers():
    """Load customer names from customers.txt."""
    try:
        if not CUSTOMERS_FILE.exists():
            return []
        text = CUSTOMERS_FILE.read_text(encoding="utf-8")
        # Filter out comments and empty lines
        return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    except Exception as e:
        print(f"Error loading customers: {e}")
        return []

def save_customers(customers):
    """Save customer names to customers.txt."""
    try:
        # Keep comments? For simplicity, we just overwrite with the list.
        # Use a header comment.
        content = "# Add one customer name per line\n" + "\n".join(customers)
        CUSTOMERS_FILE.write_text(content, encoding="utf-8")
    except Exception as e:
        print(f"Error saving customers: {e}")

def add_subscriber_via_api(email: str) -> tuple[bool, str]:
    """
    Call your existing API to add a subscriber.
    Returns (ok, message_text).
    """
    try:
        url = f"{NEWSAPI_LIST_URL}&add={email}"
        r = requests.get(url, timeout=10)
        text = r.text.strip()
        if r.ok and ("Added" in text or "added" in text or "✅" in text):
            return True, text or f"Added {email}"
        return False, text or f"API returned {r.status_code}"
    except Exception as e:
        return False, f"Exception: {e}"


def remove_subscriber_via_api(email: str) -> tuple[bool, str]:
    """
    Call your existing API to remove a subscriber.
    Returns (ok, message_text).
    """
    try:
        url = f"{NEWSAPI_LIST_URL}&remove={email}"
        r = requests.get(url, timeout=10)
        text = r.text.strip()
        if r.ok and ("Removed" in text or "removed" in text or "🗑️" in text):
            return True, text or f"Removed {email}"
        return False, text or f"API returned {r.status_code}"
    except Exception as e:
        return False, f"Exception: {e}"


def get_activity():
    """Return summary from log + data file."""
    result = {
        "last_daily": "Unknown",
        "last_weekly": "Unknown",
        "articles": 0,
        "next_monday": (datetime.now() + timedelta(days=(7 - datetime.now().weekday()))).strftime("%Y-%m-%d"),
    }

    # Check log file timestamps
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            for line in reversed(lines):
                if "daily" in line.lower():
                    result["last_daily"] = line.strip()
                    break
            for line in reversed(lines):
                if "weekly" in line.lower():
                    result["last_weekly"] = line.strip()
                    break
        except Exception:
            pass

    # Count articles
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                db = json.load(f)
                result["articles"] = len(db)
        except Exception:
            pass

    return result


# ---------------- ACTIONS ----------------
def run_job(mode):
    """
    Trigger python rss_daily_summary.py manually using the venv python.
    """
    cmd = [VENV_PY, f"{NEWSBOT_PATH}/rss_daily_summary.py", mode]
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=600)
        return output.decode("utf-8", errors="replace")
    except subprocess.CalledProcessError as e:
        return f"❌ Error running job: {e.output.decode('utf-8', errors='replace')}"
    except Exception as e:
        return f"❌ Exception: {e}"


def check_cron():
    """
    Return the last 20 lines from /opt/newsbot/run.log
    (Equivalent to: tail -n 20 /opt/newsbot/run.log)
    """
    log_path = "/opt/newsbot/run.log"
    try:
        if not os.path.exists(log_path):
            return f"❌ Log file not found: {log_path}"

        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        if not lines:
            return "<empty log file>"

        # Return last 20 lines
        return "".join(lines[-20:]).strip()

    except PermissionError:
        return f"⚠️ Permission denied reading {log_path}. Try: sudo chmod +r {log_path}"
    except Exception as e:
        return f"❌ Error reading log file: {e}"

# ---------------- ROUTES ----------------
@app.route("/")
@app.route("/admin/")
@requires_auth
def admin_home():
    subs = load_subscribers()
    customers = load_customers()
    stats = get_activity()

    html = render_template_string("""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="utf-8">
        <title>Newsbot Admin Dashboard</title>
        <style>
            body { font-family: Arial, sans-serif; background: #f8fafc; color: #111; margin: 0; padding: 0; }
            .container { max-width: 900px; margin: 40px auto; background: #fff; border-radius: 8px;
                         box-shadow: 0 4px 10px rgba(0,0,0,0.1); padding: 30px; }
            h1 { color: #1f2937; margin-bottom: 1rem; }
            h2 { color: #2563eb; margin-top: 2rem; }
            table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
            th, td { padding: 10px; text-align: left; border-bottom: 1px solid #e5e7eb; }
            th { background: #f3f4f6; }
            tr:hover td { background: #f9fafb; }
            input[type=email], input[type=text] { padding: 8px; border: 1px solid #ccc; border-radius: 6px; width: 70%; }
            button {
                background-color: #2563eb; color: white; border: none;
                padding: 8px 14px; border-radius: 6px; cursor: pointer;
            }
            button:hover { background-color: #1e40af; }
            .remove-btn { background:#dc2626; }
            .remove-btn:hover { background:#991b1b; }
            pre { background:#f3f4f6; padding:10px; border-radius:6px; overflow-x:auto; white-space:pre-wrap; }
            .muted { color:#6b7280; }
        </style>
    </head>
    <body>
    <div class="container">
        <h1>📋 HCLS Newsbot — Admin Dashboard</h1>

        <h2>System Status</h2>
        <p><b>Last Daily Run:</b> {{ stats['last_daily'] }}<br>
           <b>Last Weekly Run:</b> {{ stats['last_weekly'] }}<br>
           <b>Total Articles Collected:</b> {{ stats['articles'] }}<br>
           <b>Next Monday Email:</b> {{ stats['next_monday'] }}</p>

        <div style="margin:15px 0;">
            <button onclick="runJob('daily')">🔁 Run Daily Job</button>
            <button onclick="runJob('weekly')">🧾 Run Weekly Digest</button>
            <button onclick="runJob('send-test')">✉️ Send Test Email</button>
            <button onclick="checkCron()">⏱️ Check Cron Status</button>
        </div>

        <div id="jobResult"><pre style="display:none;"></pre></div>

        <hr>
        <h2>Subscribers</h2>
        <form id="addForm" onsubmit="return addEmail(event)" style="margin-bottom: 20px;">
            <input type="email" id="email" placeholder="Add new subscriber (email@domain.com)" required>
            <button type="submit">Add</button>
            <span id="msg" class="muted"></span>
        </form>

        <table id="subTable">
            <tr><th>#</th><th>Email</th><th>Action</th></tr>
            {% for i, email in enumerate(subs, 1) %}
            <tr>
                <td>{{ i }}</td>
                <td>{{ email }}</td>
                <td><button class="remove-btn" onclick="removeEmail('{{ email }}')">Remove</button></td>
            </tr>
            {% endfor %}
        </table>
        {% if not subs %}
        <p class="muted">No subscribers yet.</p>
        {% endif %}

        <hr>
        <h2>Our Customers</h2>
        <form id="addCustForm" onsubmit="return addCustomer(event)" style="margin-bottom: 20px;">
            <input type="text" id="custName" placeholder="Add customer name (e.g. Pfizer)" required>
            <button type="submit">Add</button>
            <span id="custMsg" class="muted"></span>
        </form>

        <table id="custTable">
            <tr><th>#</th><th>Name</th><th>Action</th></tr>
            {% for i, name in enumerate(customers, 1) %}
            <tr>
                <td>{{ i }}</td>
                <td>{{ name }}</td>
                <td><button class="remove-btn" onclick="removeCustomer('{{ name }}')">Remove</button></td>
            </tr>
            {% endfor %}
        </table>
        {% if not customers %}
        <p class="muted">No customers defined.</p>
        {% endif %}

    </div>

    <script>
    async function addEmail(e) {
        e.preventDefault();
        const email = document.getElementById('email').value.trim();
        const msg = document.getElementById('msg');
        msg.textContent = 'Adding...';
        try {
          // current UI uses GET style
          const res = await fetch(`/admin/api/add?email=${encodeURIComponent(email)}`);
          const data = await res.json();
          msg.textContent = data.message || data.error || 'Done';
          setTimeout(()=>window.location.reload(), 800);
        } catch (err) {
          msg.textContent = 'Failed.';
        }
    }

    async function removeEmail(email) {
        if (!confirm(`Remove ${email}?`)) return;
        try {
          const res = await fetch(`/admin/api/remove?email=${encodeURIComponent(email)}`);
          await res.json();
        } catch (e) {}
        window.location.reload();
    }

    async function addCustomer(e) {
        e.preventDefault();
        const name = document.getElementById('custName').value.trim();
        const msg = document.getElementById('custMsg');
        msg.textContent = 'Adding...';
        try {
          const res = await fetch(`/admin/api/add_customer?name=${encodeURIComponent(name)}`);
          const data = await res.json();
          msg.textContent = data.message || data.error || 'Done';
          setTimeout(()=>window.location.reload(), 800);
        } catch (err) {
          msg.textContent = 'Failed.';
        }
    }

    async function removeCustomer(name) {
        if (!confirm(`Remove ${name}?`)) return;
        try {
          const res = await fetch(`/admin/api/remove_customer?name=${encodeURIComponent(name)}`);
          await res.json();
        } catch (e) {}
        window.location.reload();
    }

    async function runJob(mode) {
        const pre = document.querySelector('#jobResult pre');
        pre.style.display = 'block';
        pre.textContent = 'Running ' + mode + '...';
        const res = await fetch(`/admin/api/run?mode=${encodeURIComponent(mode)}`);
        const data = await res.json();
        pre.textContent = data.output;
    }

    async function checkCron() {
        const pre = document.querySelector('#jobResult pre');
        pre.style.display = 'block';
        pre.textContent = 'Checking cron status...';
        const res = await fetch(`/admin/api/cron`);
        const data = await res.json();
        pre.textContent = data.output;
    }
    </script>
    </body>
    </html>
    """, subs=subs, customers=customers, stats=stats)
    return html


# ---------------- API ROUTES (UI uses GET) ----------------
@app.route("/admin/api/add")
@requires_auth
def api_add():
    email = (request.args.get("email") or "").strip().lower()
    if not email:
        return jsonify({"message": "Missing email"}), 400
    ok, text = add_subscriber_via_api(email)
    return jsonify({"message": f"{'✅ Added' if ok else '❌ Failed'} — {text}"})


@app.route("/admin/api/remove")
@requires_auth
def api_remove():
    email = (request.args.get("email") or "").strip().lower()
    if not email:
        return jsonify({"message": "Missing email"}), 400
    ok, text = remove_subscriber_via_api(email)
    return jsonify({"message": f"{'🗑️ Removed' if ok else '❌ Failed'} — {text}"})


@app.route("/admin/api/add_customer")
@requires_auth
def api_add_customer():
    name = (request.args.get("name") or "").strip()
    if not name:
        return jsonify({"message": "Missing name"}), 400
    customers = load_customers()
    # duplicate check
    if any(c.lower() == name.lower() for c in customers):
         return jsonify({"message": f"ℹ️ {name} already exists"})
    customers.append(name)
    save_customers(customers)
    return jsonify({"message": f"✅ Added {name}"})


@app.route("/admin/api/remove_customer")
@requires_auth
def api_remove_customer():
    name = (request.args.get("name") or "").strip()
    if not name:
        return jsonify({"message": "Missing name"}), 400
    customers = load_customers()
    if name not in customers:
         return jsonify({"message": f"⚠️ {name} not found"})
    customers = [c for c in customers if c != name]
    save_customers(customers)
    return jsonify({"message": f"🗑️ Removed {name}"})


@app.route("/admin/api/run")
@requires_auth
def api_run():
    mode = request.args.get("mode")
    if mode not in ["daily", "weekly", "send-test"]:
        return jsonify({"output": "Invalid mode."})
    output = run_job(mode)
    return jsonify({"output": output})


@app.route("/admin/api/cron")
@requires_auth
def api_cron():
    output = check_cron()
    return jsonify({"output": output})


# (Optional) POST JSON versions — future-proofing; unused by current UI
@app.route("/api/add_subscriber", methods=["POST"])
@requires_auth
def add_subscriber_post():
    try:
        data = request.get_json(force=True)
        email = (data.get("email") or "").strip().lower()
        if not email:
            return jsonify({"error": "Email missing"}), 400
        ok, text = add_subscriber_via_api(email)
        if ok:
            return jsonify({"message": f"✅ Added {email}"})
        return jsonify({"error": f"❌ {text}"}), 500
    except Exception as e:
        return jsonify({"error": f"❌ Exception while adding: {e}"}), 500


@app.route("/api/remove_subscriber", methods=["POST"])
@requires_auth
def remove_subscriber_post():
    try:
        data = request.get_json(force=True)
        email = (data.get("email") or "").strip().lower()
        if not email:
            return jsonify({"error": "Email missing"}), 400
        ok, text = remove_subscriber_via_api(email)
        if ok:
            return jsonify({"message": f"🗑️ Removed {email}"})
        return jsonify({"error": f"❌ {text}"}), 500
    except Exception as e:
        return jsonify({"error": f"❌ Exception while removing: {e}"}), 500


if __name__ == "__main__":
    # For local debug only; in production use gunicorn via systemd.
    app.run(host="127.0.0.1", port=5002, debug=True)
