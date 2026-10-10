import sqlite3, time
c = sqlite3.connect("data/runs.db")
rows = c.execute(
    "select journey, status, count(*) from samples "
    "where journey is not null and ts > ? group by 1, 2 order by 1, 2",
    (time.time() - 3 * 3600,),
).fetchall()
if not rows:
    print("No journey rows in the last 3 hours.")
for r in rows:
    print(r)