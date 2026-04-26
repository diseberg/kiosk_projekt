import sqlite3
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timezone
import argparse
import os
import sys
import time

SHEET_NAME = "KioskTest"
JSON_KEY = "credentials.json"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "checkins.db")
LARTIMMAR_DB_PATH = os.environ.get("LARTIMMAR_DB_PATH") or os.path.join(BASE_DIR, "lartimmar.db")
LARTIMMAR_SHEET = "Lartimmar"


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
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(
        resolve_credentials_file(), scopes=scopes
    )
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

    # CLEANUP: Reset any rows stuck in processing state (2) from previous crashes
    cursor.execute("UPDATE checkins SET exported = 0 WHERE exported = 2")

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


def ensure_lartimmar_table():
    conn = sqlite3.connect(LARTIMMAR_DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS lartimmar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            aktivitet TEXT,
            namn TEXT,
            personnummer TEXT,
            antal_timmar REAL,
            ledare INTEGER DEFAULT 0,
            exported INTEGER DEFAULT 0
        )
        """
    )
    cursor.execute("PRAGMA table_info(lartimmar)")
    cols = {row[1] for row in cursor.fetchall()}
    if "exported" not in cols:
        cursor.execute("ALTER TABLE lartimmar ADD COLUMN exported INTEGER DEFAULT 0")
    # Reset any rows stuck in processing state from previous crashes
    cursor.execute("UPDATE lartimmar SET exported = 0 WHERE exported = 2")
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

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
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
        now = datetime.now(timezone.utc).isoformat()
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
    
    # Acquire file lock to prevent multiple workers from exporting simultaneously
    lock_file_path = os.path.join(BASE_DIR, "export.lock")
    lock_file = None
    
    try:
        lock_file = open(lock_file_path, "w")
        # Try to acquire exclusive lock (non-blocking)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError):
            # Another process is already exporting
            print("Export already in progress by another worker, skipping...")
            lock_file.close()
            return
        
        # Use longer timeout for slow systems (30 seconds instead of default 5)
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        cursor = conn.cursor()
        
        # CLEANUP: Reset any rows stuck at exported=2 from previous crashes/timeouts
        # This ensures rows aren't lost if export failed partway through
        cursor.execute("UPDATE checkins SET exported = 0 WHERE exported = 2")
        stuck_count = cursor.rowcount
        if stuck_count > 0:
            print(f"Reset {stuck_count} rows stuck in processing state")
        conn.commit()
        
        # 0 = EJ EXPORTERAD
        # 1 = EXPORTERAD
        # 2 = BEARBETAS (Låser raderna så ingen annan tråd tar dem)

        # STEP 1: Claim rows by marking them as processing (2)
        # Now with file lock + cleanup, we safely claim rows
        cursor.execute("UPDATE checkins SET exported = 2 WHERE exported = 0")
        if cursor.rowcount == 0:
            # Nothing to process
            conn.commit()
            conn.close()
            return

        conn.commit() # Commit the claim immediately

        # STEP 2: Fetch only the rows we just claimed
        cursor.execute(
            "SELECT c.id, c.name, m.year_of_birth, c.timestamp, c.person_id, c.checkin_type "
            "FROM checkins c LEFT JOIN members m ON lower(trim(c.name)) = lower(trim(m.name)) "
            "WHERE c.exported = 2"
        )
        rows = cursor.fetchall()
        
        if not rows:
            # Should hopefully not happen if rowcount > 0, but good safety
            conn.close()
            return

        # Prepare rows
        # Format: Name, ID (Year or PersonID), Type (Avgiftstyp or "engångsavgift"), Timestamp, Date, Hour
        data_to_upload = []
        ids_to_finalize = []

        for row in rows:
            c_id, c_name, m_year, c_timestamp, c_person_id, c_checkin_type = row
            ids_to_finalize.append(c_id)
            
            name = c_name
            
            # Determine ID and Type
            if c_checkin_type == "engångsavgift":
                id_val = c_person_id if c_person_id else ""
                type_val = "engångsavgift"
            else:
                id_val = m_year if m_year is not None else ""
                type_val = "" 

            # Derive date and hour (YYYY-MM-DD, HH:00) to ease pivots in Sheets
            date_part = ""
            hour_part = ""
            if c_timestamp:
                try:
                    parts = c_timestamp.split(" ")
                    if len(parts) >= 2:
                        date_part = parts[0]
                        time_part = parts[1]
                        hour_part = time_part.split(":")[0] + ":00"
                except Exception:
                    pass

            data_to_upload.append([name, id_val, type_val, c_timestamp, date_part, hour_part])

        # STEP 3: Upload to Google Sheets
        # Add retry logic for slow/unreliable network
        max_upload_retries = 3
        upload_success = False
        
        for upload_attempt in range(max_upload_retries):
            try:
                client = get_gsheet_client()
                sh = client.open(SHEET_NAME)
                try:
                    sheet = sh.worksheet("Logg")
                    # Check if header exists, otherwise add it
                    # Read cell A1. If empty or not "name", assume missing header.
                    # (Note: This is a simple check. If the sheet is completely empty, A1 is empty)
                    val_a1 = sheet.acell('A1').value
                    if not val_a1 or val_a1.lower() != "name":
                        # If overwrite risk, maybe insert row? Or just append header?
                        # If sheet is empty, append_row will put it at the top.
                        print("Adding missing header to Logg sheet.")
                        sheet.insert_row(["name", "id", "type", "timestamp", "date", "hour"], index=1)
                except gspread.WorksheetNotFound:
                    sheet = sh.add_worksheet("Logg", rows=1000, cols=10)
                    sheet.append_row(["name", "id", "type", "timestamp", "date", "hour"]) 

                sheet.append_rows(data_to_upload)
                upload_success = True
                break  # Success, exit retry loop
            except Exception as upload_err:
                print(f"Upload attempt {upload_attempt + 1}/{max_upload_retries} failed: {upload_err}")
                if upload_attempt < max_upload_retries - 1:
                    time.sleep(2)  # Wait before retry
                else:
                    raise  # Re-raise if all retries exhausted
        
        if not upload_success:
            raise Exception("Failed to upload to Google Sheets after all retries")

        # STEP 4: Mark as Done (1)
        cursor.executemany("UPDATE checkins SET exported = 1 WHERE id = ?", [(i,) for i in ids_to_finalize])
        conn.commit()

        print(f"Exporterat {len(data_to_upload)} nya rader!")
        log_sync("write", "Logg", rows=len(data_to_upload), status="ok")

    except Exception as e:
        print(f"Fel vid export: {e}")
        # Rollback claimed rows to 0 so they can be tried again
        try:
            # Only attempt rollback if we have a valid connection
            if 'conn' in locals() and conn:
                if 'cursor' not in locals():
                    cursor = conn.cursor()
                cursor.execute("UPDATE checkins SET exported = 0 WHERE exported = 2")
                conn.commit()
        except Exception as rollback_err:
            print(f"Rollback failed: {rollback_err}")
        
        log_sync("write", "Logg", rows=0, status="error", note=str(e))
    finally:
        if 'conn' in locals() and conn:
            conn.close()
        # Release file lock
        if lock_file:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                lock_file.close()
            except Exception:
                pass


def export_new_lartimmar():
    """Export new Lartimmar rows from the local DB to the Google Sheet."""
    ensure_lartimmar_table()

    lock_file_path = os.path.join(BASE_DIR, "export_lartimmar.lock")
    lock_file = None
    conn = None

    try:
        lock_file = open(lock_file_path, "w")
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError):
            print("Lartimmar export already in progress, skipping...")
            lock_file.close()
            return

        conn = sqlite3.connect(LARTIMMAR_DB_PATH, timeout=30.0)
        cursor = conn.cursor()

        # Reset rows stuck in processing state
        cursor.execute("UPDATE lartimmar SET exported = 0 WHERE exported = 2")
        conn.commit()

        # Claim rows
        cursor.execute("UPDATE lartimmar SET exported = 2 WHERE exported = 0")
        if cursor.rowcount == 0:
            conn.commit()
            return
        conn.commit()

        cursor.execute(
            "SELECT id, timestamp, aktivitet, namn, personnummer, antal_timmar, ledare "
            "FROM lartimmar WHERE exported = 2"
        )
        rows = cursor.fetchall()
        if not rows:
            return

        data_to_upload = []
        ids_to_finalize = []
        for r in rows:
            r_id, ts, aktivitet, namn, personnummer, timmar, ledare = r
            ids_to_finalize.append(r_id)
            data_to_upload.append([
                ts or "",
                aktivitet or "",
                namn or "",
                personnummer or "",
                "" if timmar is None else float(timmar),
                "Ja" if ledare else "Nej",
            ])

        # Upload with retry
        max_upload_retries = 3
        upload_success = False
        for attempt in range(max_upload_retries):
            try:
                client = get_gsheet_client()
                sh = client.open(SHEET_NAME)
                try:
                    sheet = sh.worksheet(LARTIMMAR_SHEET)
                    val_a1 = sheet.acell('A1').value
                    if not val_a1 or val_a1.lower() != "timestamp":
                        print(f"Adding missing header to {LARTIMMAR_SHEET} sheet.")
                        sheet.insert_row(
                            ["timestamp", "aktivitet", "namn", "personnummer", "antal_timmar", "ledare"],
                            index=1,
                        )
                except gspread.WorksheetNotFound:
                    sheet = sh.add_worksheet(LARTIMMAR_SHEET, rows=1000, cols=10)
                    sheet.append_row(
                        ["timestamp", "aktivitet", "namn", "personnummer", "antal_timmar", "ledare"]
                    )

                sheet.append_rows(data_to_upload)
                upload_success = True
                break
            except Exception as upload_err:
                print(f"Lartimmar upload attempt {attempt + 1}/{max_upload_retries} failed: {upload_err}")
                if attempt < max_upload_retries - 1:
                    time.sleep(2)
                else:
                    raise

        if not upload_success:
            raise Exception("Failed to upload Lartimmar to Google Sheets after all retries")

        cursor.executemany(
            "UPDATE lartimmar SET exported = 1 WHERE id = ?",
            [(i,) for i in ids_to_finalize],
        )
        conn.commit()

        print(f"Exporterat {len(data_to_upload)} nya l\u00e4rtimmar-rader!")
        log_sync("write", LARTIMMAR_SHEET, rows=len(data_to_upload), status="ok")

    except Exception as e:
        print(f"Fel vid l\u00e4rtimmar-export: {e}")
        try:
            if conn:
                cur = conn.cursor()
                cur.execute("UPDATE lartimmar SET exported = 0 WHERE exported = 2")
                conn.commit()
        except Exception as rollback_err:
            print(f"Lartimmar rollback failed: {rollback_err}")
        log_sync("write", LARTIMMAR_SHEET, rows=0, status="error", note=str(e))
    finally:
        if conn:
            conn.close()
        if lock_file:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                lock_file.close()
            except Exception:
                pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync members and export checkins to Google Sheets")
    parser.add_argument("action", nargs="?", choices=["import-members", "export-new-rows", "export-lartimmar", "init-db", "sync-all", "reset-exports"], default="sync-all", help="Action to perform")
    args = parser.parse_args()

    if args.action == "import-members":
        import_members_from_sheet()
    elif args.action == "export-new-rows":
        export_new_rows()
    elif args.action == "export-lartimmar":
        export_new_lartimmar()
    elif args.action == "init-db":
        ensure_tables()
        ensure_lartimmar_table()
        print("Database initialized.")
    elif args.action == "sync-all":
        import_members_from_sheet()
        export_new_rows()
        export_new_lartimmar()
    elif args.action == "reset-exports":
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("UPDATE checkins SET exported = 0")
        conn.commit()
        conn.close()
        print("Done. All rows have been reset to 'unexported'. Clear the 'Logg' sheet in Google Sheets and run 'python sync_members.py' to re-export everything.")
