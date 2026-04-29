"""One-off script to drop clinicians and chat_sessions tables."""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / "backend" / ".env")

async def main() -> None:
    from sqlalchemy import text
    from app.db.base import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        await session.execute(text("DROP TABLE IF EXISTS chat_sessions CASCADE"))
        await session.execute(text("DROP TABLE IF EXISTS clinicians CASCADE"))
        await session.commit()
    print("Dropped: clinicians, chat_sessions")

asyncio.run(main())
