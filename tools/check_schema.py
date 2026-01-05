import sqlite3
import os

DB_PATH = 'checkins.db'

if not os.path.exists(DB_PATH):
    print(f"Database {DB_PATH} not found.")
else:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute("PRAGMA table_info(checkins)")
        columns = c.fetchall()
        print("Columns in checkins table:")
        for col in columns:
            print(col)
    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()
