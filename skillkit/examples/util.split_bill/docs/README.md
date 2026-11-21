# util.split_bill

Splits a bill plus tip between people to the cent, in exact integer cents (no floats). Hand-written example for the JARVIS Skill Development Kit.

Try: *split $120 among 4 people with a 20% tip*, or just *split this three ways* (JARVIS asks for the amount).

Rounding: tip on the pre-tip total, total rounded half-up to the cent, leftover cents to the first people (shares differ by at most 1 cent).

Install: see docs/learning/SKILL-INSTALL-GUIDE.md. Rollback: `python manage.py skill rollback util.split_bill` or `disable`.
