import os
import sqlite3

db = os.environ.get("TOOLS_DATA_DIR", "/opt/tools/data") + "/auth.db"
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
# Check sessions
rows = conn.execute(
    "SELECT substr(s.token,1,12) as prefix, u.display_name, s.last_seen_at "
    "FROM sessions s JOIN users u ON s.user_id=u.id "
    "ORDER BY s.last_seen_at DESC LIMIT 5"
).fetchall()
if not rows:
    print("NO SESSIONS FOUND")
else:
    for r in rows:
        print(f"{r['prefix']}...  {r['display_name']}  last_seen={r['last_seen_at']}")

# Check users
print("\nUsers:")
for u in conn.execute(
    "SELECT id, display_name, is_admin, is_active FROM users"
).fetchall():
    print(f"  {u[1]}  admin={u[2]}  active={u[3]}")

# Check apps
print("\nApps:")
for a in conn.execute("SELECT id, name, is_active FROM apps").fetchall():
    print(f"  {a[0]}  name={a[1]}  active={a[2]}")

# Check user_app_access
print("\nApp access:")
for r in conn.execute("SELECT user_id, app_id FROM user_app_access").fetchall():
    print(f"  user={r[0][:8]}...  app={r[1]}")

# Test the full SWPPP API with a real token via localhost
if rows:
    token = conn.execute(
        "SELECT token FROM sessions ORDER BY last_seen_at DESC LIMIT 1"
    ).fetchone()[0]
    import urllib.request

    req = urllib.request.Request(
        "http://127.0.0.1:8002/swppp/api/form-schema",
        headers={"Cookie": f"tools_session={token}"},
    )
    try:
        resp = urllib.request.urlopen(req)
        print(f"\nSWPPP API test: {resp.status} (body {len(resp.read())} bytes)")
    except Exception as e:
        print(f"\nSWPPP API test FAILED: {e}")

    # Also test through nginx
    req2 = urllib.request.Request(
        "https://sw3p.pro/swppp/api/form-schema",
        headers={"Cookie": f"tools_session={token}"},
    )
    import ssl

    ctx = ssl.create_default_context()
    try:
        resp2 = urllib.request.urlopen(req2, context=ctx)
        print(f"Nginx SWPPP test: {resp2.status} (body {len(resp2.read())} bytes)")
    except Exception as e:
        print(f"Nginx SWPPP test FAILED: {e}")

conn.close()
