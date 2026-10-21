"""Read-only count comparison between hardware_inventory.db and Supabase."""
import os
import sqlite3
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parent
SQLITE_DB = Path(os.environ.get("SQLITE_DB_PATH", str(ROOT / "hardware_inventory.db")))
DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise SystemExit("ERROR: Set DATABASE_URL first.")

src = sqlite3.connect(str(SQLITE_DB))
pg = psycopg.connect(DATABASE_URL)
try:
    tables = [r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    mismatch = False
    with pg.cursor() as cur:
        print("Table                         SQLite   Supabase   Result")
        print("-" * 62)
        for table in tables:
            local_count = src.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table)))
            remote_count = cur.fetchone()[0]
            ok = local_count == remote_count
            mismatch |= not ok
            print(f"{table:<29} {local_count:>6}   {remote_count:>8}   {'OK' if ok else 'MISMATCH'}")
    if mismatch:
        raise SystemExit("Verification failed: one or more row counts differ.")
    print("\nAll application-table row counts match.")
finally:
    src.close()
    pg.close()
