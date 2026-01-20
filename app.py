import os
import sqlite3
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, render_template, request, jsonify, send_from_directory
from datetime import datetime
import json
import socket
import threading
import time
import sync_members

app = Flask(__name__)

DB_PATH = os.path.join(app.root_path, 'checkins.db')

def get_ip_address():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't even have to be reachable
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

# Serve theme assets (fonts/images) from the `theme/` folder
@app.route('/theme/<path:filename>')
def theme_static(filename):
    return send_from_directory(os.path.join(app.root_path, 'theme'), filename)

# Inställningar för Google Sheets
SHEET_NAME = "KioskTest" # ÄNDRA TILL NAMNET PÅ DITT SHEET
JSON_KEY = "credentials.json"

def resolve_credentials_file():
    # try configured name first, then common accidental double-suffix
    candidates = [
        os.path.join(app.root_path, JSON_KEY),
        os.path.join(app.root_path, JSON_KEY + ".json"),
        os.path.join(app.root_path, "credentials.json.json"),
        JSON_KEY,
        JSON_KEY + ".json",
        "credentials.json.json",
    ]
    for fn in candidates:
        if os.path.exists(fn):
            if fn != JSON_KEY:
                print(f"Using credentials file: {fn}")
            return fn
    print(f"Credentials file not found. Tried: {', '.join(candidates)}")
    return JSON_KEY

def get_members_from_sheets():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        keyfile = resolve_credentials_file()
        creds = ServiceAccountCredentials.from_json_keyfile_name(keyfile, scope)
        client = gspread.authorize(creds)
        sh = client.open(SHEET_NAME)

        # Preferred: worksheet "Members" with headers (name + year)
        try:
            ws = sh.worksheet("Members")
            all_values = ws.get_all_values()
            if all_values and len(all_values) >= 2:
                header = [h.strip().lower() for h in all_values[0]]
                rows = all_values[1:]
                out = []
                for r in rows:
                    data = {header[i]: (r[i].strip() if i < len(r) else "") for i in range(len(header))}
                    name = data.get("name") or data.get("full name") or data.get("fullname")
                    yob = data.get("year") or data.get("year_of_birth") or data.get("yob")
                    m_type = data.get("avgiftstyp") or data.get("type") or data.get("membership") or data.get("membership_type") or ""
                    if not name:
                        continue
                    year_int = int(yob) if yob and yob.isdigit() else None
                    out.append({"name": name, "year": year_int, "avgiftstyp": m_type})
                return out
        except gspread.WorksheetNotFound:
            pass

        # Fallback: first worksheet, column A names only
        sheet1 = sh.sheet1
        names = sheet1.col_values(1)[1:]
        return [{"name": n, "year": None, "avgiftstyp": ""} for n in names if n]
    except Exception as e:
        print(f"Fel vid hämtning från Sheets: {e}")
        return []


def ensure_checkins_schema():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS checkins
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, timestamp TEXT, exported INTEGER DEFAULT 0, person_id TEXT, checkin_type TEXT)''')
    c.execute("PRAGMA table_info(checkins)")
    cols = {row[1] for row in c.fetchall()}
    if 'exported' not in cols:
        c.execute("ALTER TABLE checkins ADD COLUMN exported INTEGER DEFAULT 0")
    if 'person_id' not in cols:
        c.execute("ALTER TABLE checkins ADD COLUMN person_id TEXT")
    if 'checkin_type' not in cols:
        c.execute("ALTER TABLE checkins ADD COLUMN checkin_type TEXT")
    conn.commit()
    conn.close()

def init_db():
    ensure_checkins_schema()
    ensure_members_table()


def ensure_members_table():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS members (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 name TEXT NOT NULL,
                 year_of_birth TEXT,
                 avgiftstyp TEXT,
                 sheet_id TEXT,
                 last_updated TEXT)''')

    c.execute("PRAGMA table_info(members)")
    cols = {row[1] for row in c.fetchall()}
    if 'year_of_birth' not in cols:
        c.execute("ALTER TABLE members ADD COLUMN year_of_birth TEXT")
    if 'avgiftstyp' not in cols:
        c.execute("ALTER TABLE members ADD COLUMN avgiftstyp TEXT")
    if 'sheet_id' not in cols:
        c.execute("ALTER TABLE members ADD COLUMN sheet_id TEXT")
    if 'last_updated' not in cols:
        c.execute("ALTER TABLE members ADD COLUMN last_updated TEXT")

    conn.commit()
    conn.close()


def get_members_from_db():
    ensure_members_table()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT name, year_of_birth, avgiftstyp FROM members')
    rows = c.fetchall()
    conn.close()
    members = []
    for r in rows:
        members.append({'name': r[0], 'year': r[1], 'avgiftstyp': r[2] if r[2] else ""})
    return members

@app.route('/')
def index():
    # Try local DB first; fall back to Google Sheets if local DB is empty
    members = get_members_from_db()
    if not members:
        members = get_members_from_sheets()
    
    ip_address = get_ip_address()

    return render_template('index.html', members_json=json.dumps(members), ip_address=ip_address)

@app.route('/checkin', methods=['POST'])
def checkin():
    name = request.json.get('name')
    if not name:
        return jsonify({"status": "error", "message": "Inget namn skickades."}), 400

    # Normalize for comparison
    name_clean = name.strip()
    name_key = name_clean.casefold()

    # Validate against local DB members if available, otherwise Sheets
    # (Checking against sheets is risky if network is down; prefer local DB)
    db_members = get_members_from_db()
    if db_members:
        names = [m['name'] for m in db_members]
    else:
        # Fallback only if local DB is totally empty
        try:
            names = [m['name'] for m in get_members_from_sheets()]
        except:
            names = []

    member_keys = {m.strip().casefold() for m in names}

    if name_key not in member_keys:
        return jsonify({"status": "error", "message": "Namnet finns inte i listan."}), 400

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Retry loop for database lock
    max_retries = 5
    for attempt in range(max_retries):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30.0) # 30s timeout for slow systems
            c = conn.cursor()
            c.execute("INSERT INTO checkins (name, timestamp) VALUES (?, ?)", (name_clean, timestamp))
            conn.commit()
            conn.close()
            return jsonify({"status": "success", "message": f"Incheckad: {name_clean}"})
        except sqlite3.OperationalError as e:
            if "locked" in str(e):
                time.sleep(0.5) # Wait a bit before retrying
                continue
            else:
                return jsonify({"status": "error", "message": f"Databasfel: {e}"}), 500
        except Exception as e:
             return jsonify({"status": "error", "message": f"Okänt fel: {e}"}), 500
             
    return jsonify({"status": "error", "message": "Kunde inte spara till databasen (låst)."}), 500

@app.route('/checkin_guest', methods=['POST'])
def checkin_guest():
    name = request.json.get('name')
    person_id = request.json.get('person_id')
    
    if not name or not person_id:
        return jsonify({"status": "error", "message": "Namn och personnummer krävs."}), 400

    # Basic validation of person_id format XXXXXX-XXXX
    # (Optional: add regex validation if strict format is required)
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Retry loop for database lock
    max_retries = 5
    for attempt in range(max_retries):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30.0) # 30s timeout for slow systems
            c = conn.cursor()
            c.execute("INSERT INTO checkins (name, timestamp, person_id, checkin_type) VALUES (?, ?, ?, ?)", 
                      (name.strip(), timestamp, person_id.strip(), "engångsavgift"))
            conn.commit()
            conn.close()
            return jsonify({"status": "success", "message": f"Gäst incheckad: {name}"})
        except sqlite3.OperationalError as e:
            if "locked" in str(e):
                time.sleep(0.5)
                continue
            else:
               return jsonify({"status": "error", "message": f"Databasfel: {e}"}), 500
        except Exception as e:
            return jsonify({"status": "error", "message": f"Okänt fel: {e}"}), 500

    return jsonify({"status": "error", "message": "Kunde inte spara till databasen (låst)."}), 500

def background_sync_loop():
    # Initial sleep to allow server startup and avoid immediate collision if multiple workers start
    time.sleep(10) 
    while True:
        try:
            # Run sync every 10 minutes (600 seconds)
            print("[Background] Starting sync...")
            sync_members.import_members_from_sheet()
            sync_members.export_new_rows()
            print("[Background] Sync completed.")
        except Exception as e:
            print(f"[Background] Sync error: {e}")
        
        time.sleep(600)

# Start background sync thread
# Only start in ONE worker to prevent duplicate exports
# Use a marker file to claim the background sync role
def try_claim_background_sync():
    import sys
    marker_file_path = os.path.join(app.root_path, "background_sync.lock")
    try:
        marker_file = open(marker_file_path, "w")
        # Try to acquire exclusive lock (non-blocking)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(marker_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(marker_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Write PID for debugging
        marker_file.write(str(os.getpid()))
        marker_file.flush()
        return True, marker_file
    except (IOError, OSError):
        # Another worker already claimed it
        return False, None

claimed, marker_file = try_claim_background_sync()
if claimed:
    print(f"[Worker {os.getpid()}] Starting background sync thread")
    t = threading.Thread(target=background_sync_loop, daemon=True)
    t.start()
else:
    print(f"[Worker {os.getpid()}] Background sync already claimed by another worker")
    
if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=False)