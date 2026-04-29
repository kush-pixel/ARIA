"""Verify MED_ADJUD_TEXT stop/restart parsing in _build_med_history."""
import sys
sys.path.insert(0, "backend")

from app.services.fhir.adapter import convert_iemr_to_fhir as build_fhir_bundle

# Minimal iEMR payload — one visit with a medication that has a Restart adjudication
IEMR = {
    "MED_REC_NO": {
        "GENDER": "F",
        "DOB": "01/01/1940 00:00",
        "VISIT": [
            {
                "ADMIT_DATE": "01/21/2008 10:00",
                "PROBLEM": [],
                "VITALS": [],
                "MEDICATIONS": [
                    {
                        "MED_NAME": "SULAR",
                        "MED_DOSE": "20",
                        "MED_CODE": "143443",
                        "MED_DATE_ADDED": "03/13/2008 15:22",
                        "MED_DATE_LAST_MODIFIED": "02/26/2009 10:16",
                        "MED_ACTIVITY": "Refill",
                        "MED_STATUS": "ACTIVE",
                        "MED_ADJUD_TEXT": (
                            "SULAR 10 MG TAB ER PO: Restart; "
                            "stopped on 11/20/2008 09:00; "
                            "restarted on 02/26/2009 10:00. "
                            "Adjudicated on 02/26/2009 11:00 by Dr. Smith."
                        ),
                        "code_mappings": {},
                    },
                ],
                "ALLERGY": [],
                "PLAN": [],
                "SOCIAL_HX": [],
            }
        ],
    }
}

bundle = build_fhir_bundle(IEMR, patient_id="test_patient_1")
med_history = bundle["_aria_med_history"]

print("med_history entries:")
for e in med_history:
    print(f"  {e['date']}  activity={e['activity']}  name={e['name']}")

stop_entries   = [e for e in med_history if e["activity"] == "stop"]
restart_entries = [e for e in med_history if e["activity"] == "restart"]

assert len(stop_entries) == 1, f"Expected 1 stop entry, got {len(stop_entries)}"
assert stop_entries[0]["date"] == "2008-11-20", f"Wrong stop date: {stop_entries[0]['date']}"

assert len(restart_entries) == 1, f"Expected 1 restart entry, got {len(restart_entries)}"
assert restart_entries[0]["date"] == "2009-02-26", f"Wrong restart date: {restart_entries[0]['date']}"

print()
print("PASS: stop event at 2008-11-20, restart event at 2009-02-26")

# --- Verify _active_meds_at respects the stop window ---
from datetime import date
from app.services.generator.confirmation_generator import _active_meds_at

active_before_stop = _active_meds_at(med_history, date(2008, 11, 10))
active_during_stop = _active_meds_at(med_history, date(2008, 12, 15))
active_after_restart = _active_meds_at(med_history, date(2009, 3, 1))

sular_names = {"SULAR 20"}
print(f"Active before stop  (2008-11-10): {[n for n,_ in active_before_stop]}")
print(f"Active during stop  (2008-12-15): {[n for n,_ in active_during_stop]}")
print(f"Active after restart(2009-03-01): {[n for n,_ in active_after_restart]}")

assert any("SULAR" in n for n, _ in active_before_stop), "Sular should be active before stop"
assert not any("SULAR" in n for n, _ in active_during_stop), "Sular should be INACTIVE during stop window"
assert any("SULAR" in n for n, _ in active_after_restart), "Sular should be active after restart"

print()
print("PASS: _active_meds_at correctly excludes Sular during stop window")
