from flask import Flask, request, jsonify, redirect
from pathlib import Path
import json, hmac, hashlib, os, urllib.parse

app = Flask(__name__)

# Load local .env if present
local_dotenv = Path(".env")
if local_dotenv.exists():
    with open(local_dotenv, "r") as f:
        for line in f:
            if line.strip() and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

# Allow overriding DATA_DIR via env for local testing
DATA_DIR = Path(os.getenv("DATA_DIR", "/opt/newsbot/data"))
SUB_FILE = DATA_DIR / "subscribers.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)

SECRET = os.getenv("SUB_SECRET", "change-me-please")  # change in systemd env!
BASE_URL = os.getenv("BASE_URL", "http://172.233.187.203")
API_BASE = os.getenv("API_BASE", "/newsapi")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "subslist")  # ensure this matches your admin calls

# ---------------------- Utility functions ----------------------
def load_subs():
    """Load subscribers from JSON file, supports list or dict."""
    if not SUB_FILE.exists():
        return []
    try:
        data = json.loads(SUB_FILE.read_text(encoding="utf-8"))
        # backward compatibility: convert dict to list
        if isinstance(data, dict):
            return list(data.keys())
        return data
    except Exception as e:
        print(f"⚠️ Error loading subscribers: {e}")
        return []

def save_subs(lst):
    """Save subscriber list safely."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SUB_FILE.write_text(json.dumps(sorted(set(lst)), ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        print(f"⚠️ Error saving subscribers: {e}")
        return False

def make_token(email: str) -> str:
    """Generate secure token tied to email."""
    return hmac.new(SECRET.encode(), email.lower().encode(), hashlib.sha256).hexdigest()

def check_token(email: str, token: str) -> bool:
    """Verify token."""
    expected = make_token(email)
    return hmac.compare_digest(expected, token)

# ---------------------- Basic routes ----------------------
@app.get("/health")
def health():
    return "ok"

@app.post("/subscribe")
def subscribe():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return jsonify({"ok": False, "message": "Invalid email"}), 400
    subs = load_subs()
    if email not in subs:
        subs.append(email)
        save_subs(subs)
    return jsonify({"ok": True, "message": "Subscribed!"})

@app.get("/unsubscribe")
def unsubscribe():
    """Unsubscribe with secure token verification."""
    email = (request.args.get("email") or "").strip().lower()
    token = request.args.get("token") or ""
    if not email or not token:
        return "Invalid request.", 400
    if not check_token(email, token):
        return "Invalid or expired token.", 403
    subs = load_subs()
    if email in subs:
        subs.remove(email)
        save_subs(subs)
        return f"<h3>{email} unsubscribed successfully.</h3>", 200
    return "Email not found.", 404

# ---------------------- Admin endpoint ----------------------
@app.get("/list-subscribers")
def list_subscribers():
    """
    Secure endpoint to view or manage subscribers.
    Requires ?token=ADMIN_TOKEN in query.
    Supports:
      - GET /newsapi/list-subscribers?token=subslist
      - GET /newsapi/list-subscribers?token=subslist&add=email@example.com
      - GET /newsapi/list-subscribers?token=subslist&remove=email@example.com
    """
    token = request.args.get("token", "")
    if token != ADMIN_TOKEN:
        return jsonify({"ok": False, "error": "Unauthorized"}), 403

    subs = load_subs()
    add_email = (request.args.get("add") or "").strip().lower()
    remove_email = (request.args.get("remove") or "").strip().lower()

    # ---------- Add subscriber ----------
    if add_email:
        if "@" not in add_email:
            return jsonify({"ok": False, "error": "Invalid email"}), 400
        if add_email not in subs:
            subs.append(add_email)
            if not save_subs(subs):
                return jsonify({"ok": False, "error": "Failed to save file"}), 500
            print(f"✅ Added subscriber: {add_email}")
            return jsonify({"ok": True, "message": f"Added {add_email}", "count": len(subs), "subscribers": subs})
        else:
            return jsonify({"ok": True, "message": f"{add_email} already subscribed", "count": len(subs)})

    # ---------- Remove subscriber ----------
    if remove_email:
        if remove_email in subs:
            subs.remove(remove_email)
            if not save_subs(subs):
                return jsonify({"ok": False, "error": "Failed to save file"}), 500
            print(f"🗑️ Removed subscriber: {remove_email}")
            return jsonify({"ok": True, "message": f"Removed {remove_email}", "count": len(subs), "subscribers": subs})
        else:
            return jsonify({"ok": True, "message": f"{remove_email} not found", "count": len(subs)})

    # ---------- List subscribers ----------
    return jsonify({
        "ok": True,
        "count": len(subs),
        "subscribers": subs
    })

# ---------------------- Main ----------------------
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000)
