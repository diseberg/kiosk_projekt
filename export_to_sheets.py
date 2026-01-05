"""Backwards-compatible entrypoint.

This project now uses `sync_members.py` for both members import and checkin export.
Keeping this file so existing scheduled tasks still work.
"""

from sync_members import export_new_rows


if __name__ == "__main__":
    export_new_rows()