import os
import sqlite3
from flask import Flask, render_template, request, jsonify, send_from_directory
from datetime import datetime
import socket
import threading
import time
import sync_members

app = Flask(__name__)

# Allow tests/tools to override DB location via env var.
DB_PATH = os.environ.get('APP_DB_PATH') or os.path.join(app.root_path, 'checkins.db')
# Separate DB for Lärtimmar registrations.
LARTIMMAR_DB_PATH = os.environ.get('LARTIMMAR_DB_PATH') or os.path.join(app.root_path, 'lartimmar.db')

# Predefined activities for the Lärtimmar form. The UI also allows free text
# via the "Annat" option.
LARTIMMAR_ACTIVITIES = [
    "Kurs",
    "Möte",
    "Ledbygge",
    "Utbildning",
]

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
    ensure_lartimmar_schema()


def ensure_lartimmar_schema():
    conn = sqlite3.connect(LARTIMMAR_DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS lartimmar (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 timestamp TEXT,
                 aktivitet TEXT,
                 namn TEXT,
                 personnummer TEXT,
                 antal_timmar REAL,
                 ledare INTEGER DEFAULT 0,
                 exported INTEGER DEFAULT 0)''')
    c.execute("PRAGMA table_info(lartimmar)")
    cols = {row[1] for row in c.fetchall()}
    if 'exported' not in cols:
        c.execute("ALTER TABLE lartimmar ADD COLUMN exported INTEGER DEFAULT 0")
    conn.commit()
    conn.close()


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
    # Local DB is the source of truth; sync_members keeps it up to date.
    members = get_members_from_db()
    ip_address = get_ip_address()
    return render_template(
        'index.html',
        members=members,
        ip_address=ip_address,
        lartimmar_activities=LARTIMMAR_ACTIVITIES,
    )


@app.route('/lartimmar', methods=['POST'])
def register_lartimmar():
    payload = request.get_json(silent=True) or {}
    aktivitet = payload.get('aktivitet')
    namn = payload.get('namn')
    personnummer = payload.get('personnummer')
    antal_timmar = payload.get('antal_timmar')
    ledare = bool(payload.get('ledare'))

    # Validate strings
    if (not aktivitet or not isinstance(aktivitet, str) or not aktivitet.strip()
            or not namn or not isinstance(namn, str) or not namn.strip()
            or not personnummer or not isinstance(personnummer, str) or not personnummer.strip()):
        return jsonify({"status": "error", "message": "Aktivitet, namn och personnummer krävs."}), 400

    # Validate decimal hours
    try:
        timmar = float(antal_timmar)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Antal timmar måste vara ett tal."}), 400
    if timmar <= 0 or timmar > 24:
        return jsonify({"status": "error", "message": "Antal timmar måste vara mellan 0 och 24."}), 400

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    max_retries = 5
    for attempt in range(max_retries):
        try:
            conn = sqlite3.connect(LARTIMMAR_DB_PATH, timeout=30.0)
            c = conn.cursor()
            c.execute(
                "INSERT INTO lartimmar (timestamp, aktivitet, namn, personnummer, antal_timmar, ledare) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (timestamp, aktivitet.strip(), namn.strip(), personnummer.strip(), timmar, 1 if ledare else 0),
            )
            conn.commit()
            conn.close()
            return jsonify({"status": "success", "message": f"Lärtimmar registrerade: {namn.strip()}"})
        except sqlite3.OperationalError as e:
            if "locked" in str(e):
                time.sleep(0.5)
                continue
            return jsonify({"status": "error", "message": f"Databasfel: {e}"}), 500
        except Exception as e:
            return jsonify({"status": "error", "message": f"Okänt fel: {e}"}), 500

    return jsonify({"status": "error", "message": "Kunde inte spara till databasen (låst)."}), 500

@app.route('/checkin', methods=['POST'])
def checkin():
    payload = request.get_json(silent=True) or {}
    name = payload.get('name')
    if not name or not isinstance(name, str):
        return jsonify({"status": "error", "message": "Inget namn skickades."}), 400

    # Normalize for comparison
    name_clean = name.strip()
    if not name_clean:
        return jsonify({"status": "error", "message": "Inget namn skickades."}), 400
    name_key = name_clean.casefold()

    # Validate against the local DB only. sync_members keeps it fresh.
    db_members = get_members_from_db()
    member_keys = {m['name'].strip().casefold() for m in db_members if m.get('name')}

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
    payload = request.get_json(silent=True) or {}
    name = payload.get('name')
    person_id = payload.get('person_id')

    if (not name or not isinstance(name, str)
            or not person_id or not isinstance(person_id, str)
            or not name.strip() or not person_id.strip()):
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
            sync_members.export_new_lartimmar()
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

claimed, marker_file = (False, None)
# Only start the background sync when explicitly enabled (e.g. by the
# kiosk startup script). This keeps `import app` cheap for tests and CLIs.
if os.environ.get("KIOSK_BG_SYNC", "").lower() in ("1", "true", "yes"):
    claimed, marker_file = try_claim_background_sync()
    if claimed:
        print(f"[Worker {os.getpid()}] Starting background sync thread")
        t = threading.Thread(target=background_sync_loop, daemon=True)
        t.start()
    else:
        print(f"[Worker {os.getpid()}] Background sync already claimed by another worker")

# Run schema migrations at import time so gunicorn workers also stay in sync.
init_db()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)