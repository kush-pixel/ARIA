"""Temporary DB audit script — safe to delete after use."""
import asyncio, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / "backend" / ".env")

from sqlalchemy import func, select
from app.db.base import AsyncSessionLocal
from app.models.patient import Patient
from app.models.clinical_context import ClinicalContext
from app.models.reading import Reading
from app.models.medication_confirmation import MedicationConfirmation


async def main():
    async with AsyncSessionLocal() as s:
        p = (await s.execute(select(Patient).where(Patient.patient_id == "1091"))).scalar_one_or_none()
        if p:
            print("=== Patient ===")
            print(f"  patient_id:        {p.patient_id}")
            print(f"  risk_tier:         {p.risk_tier}")
            print(f"  risk_score:        {p.risk_score}")
            print(f"  tier_override:     {p.tier_override}")
            print(f"  monitoring_active: {p.monitoring_active}")
            print(f"  next_appointment:  {p.next_appointment}")
            print(f"  enrolled_at:       {p.enrolled_at}")

        cc = (await s.execute(select(ClinicalContext).where(ClinicalContext.patient_id == "1091"))).scalar_one_or_none()
        if cc:
            print("\n=== ClinicalContext ===")
            print(f"  active_problems:       {cc.active_problems}")
            print(f"  problem_codes:         {cc.problem_codes}")
            print(f"  current_medications:   {cc.current_medications}")
            print(f"  last_med_change:       {cc.last_med_change}")
            print(f"  last_visit_date:       {cc.last_visit_date}")
            print(f"  last_clinic_systolic:  {cc.last_clinic_systolic}")
            print(f"  last_clinic_diastolic: {cc.last_clinic_diastolic}")
            print(f"  last_clinic_pulse:     {cc.last_clinic_pulse}")
            print(f"  last_clinic_weight_kg: {cc.last_clinic_weight_kg}")
            print(f"  last_clinic_spo2:      {cc.last_clinic_spo2}")
            sc = (cc.social_context or "")[:120]
            print(f"  social_context:        {sc!r}")
            print(f"  overdue_labs:          {cc.overdue_labs}")
            print(f"  allergies:             {cc.allergies}")
            print(f"  allergy_reactions:     {cc.allergy_reactions}")
            hs = cc.historic_bp_systolic or []
            hd = cc.historic_bp_dates or []
            print(f"  historic_bp:           {len(hs)} values  min={min(hs) if hs else None}  max={max(hs) if hs else None}  mean={round(sum(hs)/len(hs),1) if hs else None}")
            print(f"  historic_bp_dates:     {len(hd)} dates  first={hd[0] if hd else None}  last={hd[-1] if hd else None}")
            mh = cc.med_history or []
            print(f"  med_history:           {len(mh)} entries")
            if mh:
                latest = sorted(mh, key=lambda x: x.get("date", ""))[-1]
                print(f"    most recent: {latest}")
            pa = cc.problem_assessments or []
            print(f"  problem_assessments:   {len(pa)} entries")
            if pa:
                print(f"    first: {pa[0]}")
            print(f"  recent_labs:           {cc.recent_labs}")

        # Readings
        r_total = (await s.execute(select(func.count()).select_from(Reading).where(Reading.patient_id == "1091"))).scalar()
        r_clinic = (await s.execute(select(func.count()).select_from(Reading).where(Reading.patient_id == "1091", Reading.source == "clinic"))).scalar()
        r_gen = (await s.execute(select(func.count()).select_from(Reading).where(Reading.patient_id == "1091", Reading.source == "generated"))).scalar()
        r_range = (await s.execute(select(func.min(Reading.effective_datetime), func.max(Reading.effective_datetime)).where(Reading.patient_id == "1091"))).one()
        r_stats = (await s.execute(select(
            func.avg(Reading.systolic_avg),
            func.min(Reading.systolic_avg),
            func.max(Reading.systolic_avg),
            func.stddev_pop(Reading.systolic_avg),
            func.avg(Reading.diastolic_avg),
        ).where(Reading.patient_id == "1091"))).one()

        print("\n=== Readings ===")
        print(f"  total:           {r_total}  (clinic={r_clinic}, generated={r_gen})")
        print(f"  date range:      {r_range[0]}  ->  {r_range[1]}")
        avg_s = round(float(r_stats[0]), 1) if r_stats[0] else None
        pstdev = round(float(r_stats[3]), 1) if r_stats[3] else None
        cv = round(float(r_stats[3]) / float(r_stats[0]) * 100, 1) if r_stats[0] and r_stats[3] else None
        print(f"  systolic_avg:    mean={avg_s}  min={r_stats[1]}  max={r_stats[2]}  pstdev={pstdev}  CV={cv}%")
        print(f"  diastolic mean:  {round(float(r_stats[4]),1) if r_stats[4] else None}")

        null_sys = (await s.execute(select(func.count()).select_from(Reading).where(Reading.patient_id == "1091", Reading.systolic_avg.is_(None)))).scalar()
        null_dia = (await s.execute(select(func.count()).select_from(Reading).where(Reading.patient_id == "1091", Reading.diastolic_avg.is_(None)))).scalar()
        print(f"  null systolic_avg: {null_sys}  null diastolic_avg: {null_dia}")

        for sess in ("morning", "evening", "ad_hoc"):
            c = (await s.execute(select(func.count()).select_from(Reading).where(Reading.patient_id == "1091", Reading.session == sess))).scalar()
            print(f"  session={sess}: {c}")

        # Spot-check recent 28-day window (what detectors see)
        from datetime import UTC, datetime, timedelta
        now = datetime.now(tz=UTC)
        window28 = now - timedelta(days=28)
        r_28d = (await s.execute(select(func.count(), func.avg(Reading.systolic_avg)).select_from(Reading).where(
            Reading.patient_id == "1091",
            Reading.effective_datetime >= window28,
        ))).one()
        print(f"  last-28d readings: {r_28d[0]}  avg_systolic={round(float(r_28d[1]),1) if r_28d[1] else None}")

        # Medication confirmations
        mc_total = (await s.execute(select(func.count()).select_from(MedicationConfirmation).where(MedicationConfirmation.patient_id == "1091"))).scalar()
        mc_confirmed = (await s.execute(select(func.count()).select_from(MedicationConfirmation).where(
            MedicationConfirmation.patient_id == "1091",
            MedicationConfirmation.confirmed_at.isnot(None),
        ))).scalar()
        mc_range = (await s.execute(select(
            func.min(MedicationConfirmation.scheduled_time),
            func.max(MedicationConfirmation.scheduled_time),
        ).where(MedicationConfirmation.patient_id == "1091"))).one()
        mc_meds = (await s.execute(
            select(MedicationConfirmation.medication_name, func.count())
            .where(MedicationConfirmation.patient_id == "1091")
            .group_by(MedicationConfirmation.medication_name)
            .order_by(func.count().desc())
        )).all()

        print("\n=== Medication Confirmations ===")
        print(f"  total:      {mc_total}")
        rate = round(mc_confirmed / mc_total * 100, 1) if mc_total else 0
        print(f"  confirmed:  {mc_confirmed}  ({rate}%)")
        print(f"  date range: {mc_range[0]}  ->  {mc_range[1]}")
        print(f"  by medication ({len(mc_meds)} drugs):")
        for name, cnt in mc_meds:
            print(f"    {name:<40} {cnt}")

        # 28-day confirmations
        mc_28_total = (await s.execute(select(func.count(), func.count(MedicationConfirmation.confirmed_at)).select_from(MedicationConfirmation).where(
            MedicationConfirmation.patient_id == "1091",
            MedicationConfirmation.scheduled_time >= window28,
        ))).one()
        rate_28 = round(float(mc_28_total[1]) / float(mc_28_total[0]) * 100, 1) if mc_28_total[0] else 0
        print(f"  last-28d: total={mc_28_total[0]}  confirmed={mc_28_total[1]}  rate={rate_28}%")


asyncio.run(main())
