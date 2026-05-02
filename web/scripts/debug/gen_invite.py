from web.auth.db import connect, create_invite

with connect() as conn:
    code = create_invite(conn, "Admin", ["swppp"], grant_admin=True)
print(f"Code: {code}")
print(f"Link: https://sw3p.pro/auth/login?code={code}")
