"""
create_db.py — One-click PostgreSQL database creation.

Usage:
    python create_db.py

Reads DATABASE_URL from .env, connects to the 'postgres' maintenance database,
then creates 'cosmetique_ai' if it doesn't already exist.
"""
import sys

from dotenv import load_dotenv
load_dotenv()

import os
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

# ── Parse DATABASE_URL ────────────────────────────────────────────────────
db_url = os.getenv("DATABASE_URL", "")
if not db_url:
    print("❌  DATABASE_URL not found in .env")
    sys.exit(1)

# Replace the target DB name with 'postgres' for connection
if "cosmetique_ai" in db_url:
    maintenance_url = db_url.replace("/cosmetique_ai", "/postgres")
    target_db = "cosmetique_ai"
else:
    print("⚠️  DATABASE_URL does not contain 'cosmetique_ai'. Aborting.")
    sys.exit(1)

print(f"🔌 Connecting to PostgreSQL...")

try:
    conn = psycopg2.connect(maintenance_url)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()

    # Check if DB already exists
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target_db,))
    if cur.fetchone():
        print(f"✅  Database '{target_db}' already exists — nothing to do.")
    else:
        cur.execute(f'CREATE DATABASE "{target_db}"')
        print(f"✅  Database '{target_db}' created successfully!")
        print(f"\nNext steps:")
        print(f"  alembic upgrade head")
        print(f"  python seed.py")
        print(f"  uvicorn app.main:app --reload")

    cur.close()
    conn.close()

except psycopg2.OperationalError as e:
    print(f"❌  Connection error: {e}")
    print(f"\nCheck DATABASE_URL in .env:")
    print(f"  postgresql://postgres:YOUR_PASSWORD@localhost:5432/cosmetique_ai")
    sys.exit(1)
