import os
import sqlite3
import unittest
import uuid


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DB_PATH = os.path.join(PROJECT_ROOT, 'checkins.db')


class KioskAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.chdir(PROJECT_ROOT)
        from app import app as flask_app
        from app import init_db, ensure_members_table

        cls.app = flask_app
        init_db()
        ensure_members_table()

        # Seed one unique member so we don't depend on Google Sheets during tests
        cls.test_member_name = f"__TEST_MEMBER__{uuid.uuid4().hex}"
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "INSERT INTO members (name, year_of_birth) VALUES (?, ?)",
            (cls.test_member_name, "1990"),
        )
        conn.commit()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        # Best-effort cleanup
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("DELETE FROM members WHERE name = ?", (cls.test_member_name,))
            c.execute("DELETE FROM checkins WHERE name = ?", (cls.test_member_name,))
            conn.commit()
            conn.close()
        except Exception:
            pass

    def test_index_renders_members_json(self):
        with self.app.test_client() as client:
            resp = client.get('/')
            self.assertEqual(resp.status_code, 200)
            html = resp.get_data(as_text=True)
            self.assertIn('id="members-data"', html)
            self.assertIn(self.test_member_name, html)

    def test_checkin_valid_and_invalid(self):
        with self.app.test_client() as client:
            ok = client.post('/checkin', json={'name': self.test_member_name})
            self.assertEqual(ok.status_code, 200)
            self.assertIn('success', ok.get_data(as_text=True))

            bad = client.post('/checkin', json={'name': 'ThisNameShouldNotExist_12345'})
            self.assertEqual(bad.status_code, 400)


if __name__ == '__main__':
    unittest.main()
