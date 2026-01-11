import sqlite3
import os
import argparse

# Determine DB path (same logic as app.py/sync_members.py)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "checkins.db")

def view_checkins(limit=50, show_all=False):
    if not os.path.exists(DB_PATH):
        print(f"Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        if show_all:
            query = "SELECT * FROM checkins ORDER BY id DESC"
            cursor.execute(query)
        else:
            query = "SELECT * FROM checkins ORDER BY id DESC LIMIT ?"
            cursor.execute(query, (limit,))
            
        rows = cursor.fetchall()
        
        print(f"{'ID':<5} | {'Name':<30} | {'Timestamp':<20} | {'Exported':<10} | {'Type':<15} | {'PersonID'}")
        print("-" * 100)
        
        for row in rows:
            # Handle schema differences (some rows might have None for new columns if they are old)
            id_val = row[0]
            name = row[1]
            timestamp = row[2]
            exported = row[3]
            
            # Check if we have the new columns (schema migration might have happened)
            # Schema: id, name, timestamp, exported, person_id, checkin_type
            person_id = row[4] if len(row) > 4 and row[4] is not None else ""
            checkin_type = row[5] if len(row) > 5 and row[5] is not None else ""
            
            # Ensure other fields are strings (handle potential None in old data)
            id_val = str(id_val) if id_val is not None else ""
            name = str(name) if name is not None else ""
            timestamp = str(timestamp) if timestamp is not None else ""
            
            # Translate exported status
            status = {0: "NO", 1: "YES", 2: "PENDING"}.get(exported, str(exported))
            
            print(f"{id_val:<5} | {name:<30} | {timestamp:<20} | {status:<10} | {checkin_type:<15} | {person_id}")

        print("\nSummary:")
        cursor.execute("SELECT count(*) FROM checkins")
        total = cursor.fetchone()[0]
        cursor.execute("SELECT count(*) FROM checkins WHERE exported=0")
        pending = cursor.fetchone()[0]
        cursor.execute("SELECT count(*) FROM checkins WHERE exported=1")
        exported_count = cursor.fetchone()[0]
        cursor.execute("SELECT count(*) FROM checkins WHERE exported=2")
        locked = cursor.fetchone()[0]
        
        print(f"Total: {total}, Pending: {pending}, Exported: {exported_count}, Locked (Processing): {locked}")
        
    except Exception as e:
        print(f"Error reading database: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="View checkins in local database")
    parser.add_argument("--all", action="store_true", help="Show all rows, not just the last 50")
    parser.add_argument("--limit", type=int, default=50, help="Number of rows to show (default 50)")
    args = parser.parse_args()
    
    view_checkins(limit=args.limit, show_all=args.all)
