"""Check synthetic reading patterns for visit 18 false negative (2009-02-26)."""
import asyncio, sys
from datetime import datetime, UTC, timedelta
sys.path.insert(0, 'backend')
from dotenv import load_dotenv
load_dotenv('backend/.env')

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
import os

async def check():
    db_url = os.environ.get('DATABASE_URL', '')
    if db_url.startswith('postgresql://'):
        db_url = db_url.replace('postgresql://', 'postgresql+asyncpg://', 1)
    elif db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql+asyncpg://', 1)
    engine = create_async_engine(db_url, echo=False)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as s:
        # Visit 18: 2009-02-26 — Sular stopped during pneumonia, BP=161
        as_of = datetime(2009, 2, 26, 23, 59, 59, tzinfo=UTC)
        ws = as_of - timedelta(days=90)

        r = await s.execute(
            text('SELECT effective_datetime,systolic_avg FROM readings '
                 'WHERE patient_id=:p AND effective_datetime>=:ws AND effective_datetime<=:ao '
                 'ORDER BY effective_datetime ASC'),
            {'p': '1091', 'ws': ws, 'ao': as_of}
        )
        rows = list(r)
        print(f"Visit 18 — 90d window ending 2009-02-26")
        print(f"Readings in window: {len(rows)}")
        if rows:
            syss = [float(x[1]) for x in rows]
            print(f"Mean: {sum(syss)/len(syss):.1f}  Min: {min(syss):.1f}  Max: {max(syss):.1f}")
            print(f"First: {rows[0][0].date()} sys={rows[0][1]}")
            print(f"Last:  {rows[-1][0].date()} sys={rows[-1][1]}")

            pts = [((x[0]-rows[0][0]).total_seconds()/86400, float(x[1])) for x in rows]
            n = len(pts)
            sx = sum(p[0] for p in pts)
            sy = sum(p[1] for p in pts)
            sxy = sum(p[0]*p[1] for p in pts)
            sx2 = sum(p[0]**2 for p in pts)
            slope = (n*sxy - sx*sy) / (n*sx2 - sx**2) if (n*sx2 - sx**2) != 0 else 0
            print(f"Slope: {slope:.4f} mmHg/day")

            rec = [float(x[1]) for x in rows if x[0] >= as_of - timedelta(days=3)]
            bas = [float(x[1]) for x in rows if as_of - timedelta(days=10) <= x[0] < as_of - timedelta(days=3)]
            sr  = [float(x[1]) for x in rows if x[0] >= as_of - timedelta(days=7)]
            so  = [float(x[1]) for x in rows if as_of - timedelta(days=21) <= x[0] < as_of - timedelta(days=14)]

            if rec: print(f"Recent 3d avg:   {sum(rec)/len(rec):.1f} (n={len(rec)})")
            if bas: print(f"Baseline 4-10d:  {sum(bas)/len(bas):.1f} (n={len(bas)})")
            if sr and so:
                print(f"Step-change: recent7d={sum(sr)/len(sr):.1f} old7d={sum(so)/len(so):.1f} delta={sum(sr)/len(sr)-sum(so)/len(so):.1f}")

            print("\nSample readings (every 10th):")
            for r2 in rows[::10]:
                print(f"  {r2[0].date()}  sys={r2[1]}")

    await engine.dispose()

asyncio.run(check())
