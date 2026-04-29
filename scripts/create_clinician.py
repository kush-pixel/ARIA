"""One-off script to create a clinician account in the database.

Usage (from backend/ directory):
    python ../scripts/create_clinician.py

Edit USERNAME and PASSWORD below before running.
"""

import asyncio
import os
import sys
import uuid
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

# Load .env before importing app modules
from dotenv import load_dotenv
load_dotenv(BACKEND_DIR / ".env")

import bcrypt

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()

# ── Edit these ────────────────────────────────────────────────────────────────
USERNAME = "dr.frank"
PASSWORD = "aria2026"
EMAIL    = "frank@aria.clinic"
# ─────────────────────────────────────────────────────────────────────────────


async def main() -> None:
    from sqlalchemy import text
    from app.db.base import engine, AsyncSessionLocal
    import app.models  # ensure all models are registered with Base

    # Step 1 — create tables if they don't exist
    from app.db.base import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, checkfirst=True)
    print("Tables ensured.")

    # Step 2 — insert / update clinician
    clinician_id = str(uuid.uuid4())
    hashed = hash_password(PASSWORD)

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("""
                INSERT INTO clinicians (clinician_id, username, email, hashed_password, role, is_active)
                VALUES (:id, :username, :email, :hashed, 'clinician', true)
                ON CONFLICT (username) DO UPDATE
                    SET hashed_password = EXCLUDED.hashed_password,
                        is_active = true
            """),
            {"id": clinician_id, "username": USERNAME, "email": EMAIL, "hashed": hashed},
        )
        await session.commit()

    print(f"Clinician '{USERNAME}' created/updated. Password: '{PASSWORD}'")


asyncio.run(main())
