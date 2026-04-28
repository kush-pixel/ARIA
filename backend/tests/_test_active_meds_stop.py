"""Verify _active_meds_at respects stop/restart entries."""
import sys
sys.path.insert(0, ".")
from datetime import date
from dotenv import load_dotenv
load_dotenv(".env")

from app.services.generator.confirmation_generator import _active_meds_at

med_history = [
    {"name": "SULAR 20", "rxnorm": None, "date": "2008-03-13", "activity": "Refill"},
    {"name": "SULAR 20", "rxnorm": None, "date": "2008-11-20", "activity": "stop"},
    {"name": "SULAR 20", "rxnorm": None, "date": "2009-02-26", "activity": "restart"},
    {"name": "LISINOPRIL 10", "rxnorm": None, "date": "2008-01-14", "activity": "Refill"},
]

before = _active_meds_at(med_history, date(2008, 11, 10))
during = _active_meds_at(med_history, date(2008, 12, 15))
after  = _active_meds_at(med_history, date(2009, 3, 1))

print(f"Before stop  (2008-11-10): {sorted(n for n,_ in before)}")
print(f"During stop  (2008-12-15): {sorted(n for n,_ in during)}")
print(f"After restart(2009-03-01): {sorted(n for n,_ in after)}")

assert any("SULAR" in n for n, _ in before),  "Sular should be active before stop"
assert not any("SULAR" in n for n, _ in during), "Sular should be INACTIVE during stop window"
assert any("SULAR" in n for n, _ in after),   "Sular should be active after restart"

# Lisinopril unaffected throughout
assert any("LISINOPRIL" in n for n, _ in before)
assert any("LISINOPRIL" in n for n, _ in during)
assert any("LISINOPRIL" in n for n, _ in after)

print()
print("PASS: _active_meds_at correctly gates Sular during stop window")
