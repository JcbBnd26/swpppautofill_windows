import os
import sqlite3

db = os.environ.get("TOOLS_DATA_DIR", "/opt/tools/data") + "/auth.db"
conn = sqlite3.connect(db)
for r in conn.execute(
    "SELECT id, display_name, status FROM invite_codes ORDER BY created_at DESC LIMIT 5"
).fetchall():
    print(r)
conn.close()
