import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'checkins.db')

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

try:
    cur.execute('SELECT COUNT(*) FROM members')
    print('members count:', cur.fetchone()[0])
    cur.execute('SELECT name, year_of_birth FROM members LIMIT 20')
    print('sample:', cur.fetchall())
except Exception as e:
    print('DB error:', e)
finally:
    conn.close()
