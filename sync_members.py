import sqlite3
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import argparse
import os

SHEET_NAME = "KioskTest"
JSON_KEY = "credentials.json"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "checkins.db")


def resolve_credentials_file():
    candidates = [
        os.path.join(BASE_DIR, JSON_KEY),
        os.path.join(BASE_DIR, JSON_KEY + ".json"),
        os.path.join(BASE_DIR, "credentials.json.json"),
        JSON_KEY,
        JSON_KEY + ".json",
        "credentials.json.json",
    ]
    for fn in candidates:
        if os.path.exists(fn):
            return fn
    return JSON_KEY


def get_gsheet_client():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(resolve_credentials_file(), scope)
    return gspread.authorize(creds)


def ensure_tables():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS checkins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            timestamp TEXT,
            exported INTEGER DEFAULT 0,
            person_id TEXT,
            checkin_type TEXT
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            year_of_birth TEXT,
            avgiftstyp TEXT,
            sheet_id TEXT,
            last_updated TEXT
        )
        """
    )

    # Migrate older schemas (CREATE TABLE IF NOT EXISTS will not add missing columns)
    cursor.execute("PRAGMA table_info(checkins)")
    checkins_cols = {row[1] for row in cursor.fetchall()}
    if "exported" not in checkins_cols:
        cursor.execute("ALTER TABLE checkins ADD COLUMN exported INTEGER DEFAULT 0")
    if "person_id" not in checkins_cols:
        cursor.execute("ALTER TABLE checkins ADD COLUMN person_id TEXT")
    if "checkin_type" not in checkins_cols:
        cursor.execute("ALTER TABLE checkins ADD COLUMN checkin_type TEXT")

    cursor.execute("PRAGMA table_info(members)")
    members_cols = {row[1] for row in cursor.fetchall()}
    if "year_of_birth" not in members_cols:
        cursor.execute("ALTER TABLE members ADD COLUMN year_of_birth TEXT")
    if "avgiftstyp" not in members_cols:
        cursor.execute("ALTER TABLE members ADD COLUMN avgiftstyp TEXT")
    if "sheet_id" not in members_cols:
        cursor.execute("ALTER TABLE members ADD COLUMN sheet_id TEXT")
    if "last_updated" not in members_cols:
        cursor.execute("ALTER TABLE members ADD COLUMN last_updated TEXT")
    conn.commit()
    conn.close()


def log_sync(action, target, rows=0, status="ok", note=""):
    try:
        client = get_gsheet_client()
        sh = client.open(SHEET_NAME)
        try:
            log_ws = sh.worksheet("SyncLog")
        except gspread.WorksheetNotFound:
            log_ws = sh.add_worksheet("SyncLog", rows=1000, cols=6)
            log_ws.append_row(["timestamp", "action", "target", "rows", "status", "note"]) 

        ts = datetime.utcnow().isoformat() + "Z"
        log_ws.append_row([ts, action, target, str(rows), status, note])
    except Exception as e:
        print(f"Could not write sync log to sheet: {e}")


def import_members_from_sheet():
    ensure_tables()
    try:
        client = get_gsheet_client()
        sh = client.open(SHEET_NAME)

        source_name = "Members"
        try:
            ws = sh.worksheet("Members")
            all_values = ws.get_all_values()
        except gspread.WorksheetNotFound:
            # Fallback to first sheet (common setup)
            source_name = "sheet1"
            ws = sh.sheet1
            all_values = ws.get_all_values()

        if not all_values:
            print("No member rows found.")
            log_sync("read", source_name, rows=0, status="empty")
            return

        # Decide whether first row is a header
        first = [c.strip() for c in (all_values[0] or [])]
        first_l = [c.lower() for c in first]
        name_keys = ("name", "full name", "fullname", "namn")
        year_keys = (
            "year",
            "year_of_birth",
            "yob",
            "birthyear",
            "födelseår",
            "fodelsear",
            "född",
            "fodd",
            "år",
            "ar",
        )
        type_keys = (
            "avgiftstyp",
            "type",
            "membership",
            "membership_type",
            "typ",
            "medlemstyp",
        )
        has_header = any(x in name_keys for x in first_l) or any(x in year_keys for x in first_l)

        if has_header:
            header = [h.strip().lower() for h in all_values[0]]
            rows = all_values[1:]
        else:
            # Assume columns: A=name, B=year_of_birth (optional)
            header = ["name", "year_of_birth"]
            rows = all_values

        # Parse first; only touch DB if we got at least one valid member.
        now = datetime.utcnow().isoformat()
        parsed = []
        for r in rows:
            name = (r[0].strip() if len(r) >= 1 else "")
            yob = (r[1].strip() if len(r) >= 2 else "")
            m_type = ""

            if has_header:
                data = {header[i]: (r[i].strip() if i < len(r) else "") for i in range(len(header))}
                name = (
                    data.get("name")
                    or data.get("full name")
                    or data.get("fullname")
                    or data.get("namn")
                    or ""
                )
                yob = (
                    data.get("year")
                    or data.get("year_of_birth")
                    or data.get("yob")
                    or data.get("birthyear")
                    or data.get("födelseår")
                    or data.get("fodelsear")
                    or data.get("född")
                    or data.get("fodd")
                    or data.get("år")
                    or data.get("ar")
                    or ""
                )
                m_type = (
                    data.get("avgiftstyp")
                    or data.get("type")
                    or data.get("membership")
                    or data.get("membership_type")
                    or data.get("typ")
                    or data.get("medlemstyp")
                    or ""
                )

            if not name:
                continue

            # Keep year as user-entered text (e.g. "1990" or "-90")
            yob_text = yob.strip() if yob is not None else ""
            if yob_text == "":
                yob_text = None
            
            # Truncate membership type to 20 chars as requested
            m_type = m_type[:20]

            parsed.append((name, yob_text, m_type, now))

        if not parsed:
            print(f"No valid members parsed from {source_name}; keeping existing local members.")
            log_sync("read", source_name, rows=0, status="empty", note="no valid parsed members")
            return

        conn = sqlite3.connect(DB_PATH)
        try:
            with conn:
                cur = conn.cursor()
                # Full refresh so local DB matches the sheet (including removals)
                cur.execute("DELETE FROM members")
                cur.executemany(
                    "INSERT INTO members (name, year_of_birth, avgiftstyp, last_updated) VALUES (?, ?, ?, ?)",
                    parsed,
                )
        finally:
            conn.close()

        print(f"Imported members from {source_name}: {len(parsed)} rows")
        log_sync("read", source_name, rows=len(parsed), status="ok")

    except Exception as e:
        print(f"Error importing members: {e}")
        log_sync("read", "Members", rows=0, status="error", note=str(e))


def export_new_rows():
    ensure_tables()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Also fetch person_id and checkin_type for guest checkins
    cursor.execute(
        "SELECT c.id, c.name, m.year_of_birth, c.timestamp, c.person_id, c.checkin_type "
        "FROM checkins c LEFT JOIN members m ON lower(trim(c.name)) = lower(trim(m.name)) "
        "WHERE c.exported = 0"
    )
    rows = cursor.fetchall()

    if not rows:
        print("Inga nya incheckningar att exportera.")
        conn.close()
        return

    try:
        # Prepare rows
        # Format: Name, ID (Year or PersonID), Type (Avgiftstyp or "engångsavgift"), Timestamp
        data_to_upload = []
        for row in rows:
            c_id, c_name, m_year, c_timestamp, c_person_id, c_checkin_type = row
            
            name = c_name
            
            # Determine ID and Type
            if c_checkin_type == "engångsavgift":
                # Guest
                id_val = c_person_id if c_person_id else ""
                type_val = "engångsavgift"
            else:
                # Member
                id_val = m_year if m_year is not None else ""
                # If we want to show member type here, we could fetch it. 
                # For now, let's leave it blank or "Medlem" if requested. 
                # The user request specifically mentioned "engångsavgift" for non-members.
                # Let's assume blank for members to match previous behavior, or maybe "Medlem"?
                # Previous behavior was just Name, Year, Timestamp.
                # Let's use "Medlem" to be explicit, or empty string.
                type_val = "" 

            data_to_upload.append([name, id_val, type_val, c_timestamp])

        ids_to_update = [row[0] for row in rows]

        client = get_gsheet_client()
        sh = client.open(SHEET_NAME)
        try:
            sheet = sh.worksheet("Logg")
        except gspread.WorksheetNotFound:
            sheet = sh.add_worksheet("Logg", rows=1000, cols=10)
            # Add header row for clarity
            sheet.append_row(["name", "id", "type", "timestamp"]) 

        sheet.append_rows(data_to_upload)

        cursor.executemany("UPDATE checkins SET exported = 1 WHERE id = ?", [(i,) for i in ids_to_update])
        conn.commit()

        print(f"Exporterat {len(data_to_upload)} nya rader!")
        log_sync("write", "Logg", rows=len(data_to_upload), status="ok")

    except Exception as e:
        print(f"Fel: {e}")
        import traceback
        print("Hela felet:")
        print(traceback.format_exc())
        log_sync("write", "Logg", rows=0, status="error", note=str(e))
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync members and export checkins to Google Sheets")
    parser.add_argument("action", nargs="?", choices=["import-members", "export-new-rows", "init-db", "sync-all"], default="sync-all", help="Action to perform")
    args = parser.parse_args()

    if args.action == "import-members":
        import_members_from_sheet()
    elif args.action == "export-new-rows":
        export_new_rows()
    elif args.action == "init-db":
        ensure_tables()
        print("Database initialized.")
    elif args.action == "sync-all":
        import_members_from_sheet()
        export_new_rows()
