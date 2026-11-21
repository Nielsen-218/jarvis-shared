"""Active-duty Army benefits: a personal checklist that makes sure you're using everything you've earned.

Jarvis keeps your service profile (pay grade, years in, married, kids, duty station, ETS, languages,
deployed...) and a status for each benefit (using / not using / not eligible / unsure). From that he
tells you what to act on next, opens a full benefits page, and nudges you at the right moments
(marriage, a baby, PCS, deployment, re-enlistment window, ETS).

Dollar amounts and caps change every year, so this guide states the rule and where to check the current
number; Jarvis looks up current figures with search_web_free when asked. Always confirm with your S1,
finance office, ACS, or the official sites listed.

Stored per person in data/users/<id>/military.json.
"""
import html
import webbrowser
from datetime import date, datetime

from core import storage
from tools.registry import tool

FILE = "military.json"
STATUSES = ["using", "not using", "not eligible", "unsure"]
PROFILE_FIELDS = ["rank", "pay_grade", "years_of_service", "married", "spouse_name", "children", "duty_station",
                  "ets", "deployed", "airborne", "languages", "legal_residence", "education_goal", "tsp_percent",
                  "pcs_soon", "lives_on_post", "efmp"]


def _yes(v) -> bool:
    return str(v).strip().lower() in ("true", "yes", "y", "1", "married")


def _num(v, default=0.0) -> float:
    try:
        return float(str(v).strip().split()[0])
    except (ValueError, IndexError):
        return default


def _grade(p) -> str:
    return str(p.get("pay_grade", "")).upper().replace("-", "").replace(" ", "")


def _days_to(iso) -> int | None:
    try:
        return (date.fromisoformat(str(iso)[:10]) - date.today()).days
    except ValueError:
        return None


def ALWAYS(p) -> bool:
    return True


def MARRIED(p) -> bool:
    return _yes(p.get("married"))


def NEAR_ETS(p) -> bool:
    d = _days_to(p.get("ets")) if p.get("ets") else None
    return d is not None and d <= 400


# key: (area, title, priority 1-3, applies(profile), what it is, how/where, next-step nudge)
BENEFITS = {
    # -- pay and money -----------------------------------------------------------------------------
    "bah_dependents": ("Pay", "BAH at the 'with dependents' rate", 1, MARRIED,
        "Basic Allowance for Housing is tax-free and set by your duty-station ZIP, pay grade and whether you have dependents. Married soldiers get the higher 'with dependents' rate.",
        "Spouse enrolled in DEERS (ID card office) and marriage certificate turned in to S1/finance. Check your LES on myPay. Rates: travel.dod.mil BAH calculator.",
        "Check your LES: BAH should show 'with dependents'. If not, take the marriage certificate to S1; it can be back-paid."),
    "bas": ("Pay", "BAS (food allowance)", 3, ALWAYS,
        "Basic Allowance for Subsistence: a tax-free monthly food allowance. Soldiers on a meal card in the barracks usually don't get the full amount.",
        "Shows on your LES. If you moved off post or married and still don't get it, see S1.",
        "Confirm BAS is on your LES."),
    "tsp_match": ("Money", "TSP match (Blended Retirement System)", 1, ALWAYS,
        "Under BRS the Army puts 1% of basic pay into your Thrift Savings Plan automatically and matches your contributions up to 4% more: put in 5% and the Army adds 5%. Less than 5% leaves free money on the table. Roth TSP is usually smart at junior grades.",
        "Change it in myPay (Thrift Savings Plan); check funds at tsp.gov. Free help: the Personal Financial Counselor at ACS.",
        "Make sure you contribute at least 5% so you get the full 5% match."),
    "continuation_pay": ("Money", "BRS Continuation Pay", 2, lambda p: 8 <= _num(p.get("years_of_service")) <= 12,
        "A one-time bonus (a multiple of your monthly basic pay) around 12 years of service if you agree to serve more years; lump sum or installments.",
        "Your career counselor / S1 explains the current multiple and the election window.",
        "You're near the continuation-pay window: ask your career counselor for the current multiple."),
    "flpp": ("Pay", "Foreign Language Proficiency Pay", 1, lambda p: bool(str(p.get("languages", "")).strip()),
        "Soldiers who test proficient in languages the Army needs can earn monthly Foreign Language Proficiency Pay. Haitian Creole and French are on DoD language lists, so Kreyol and French can pay you every month.",
        "Take the Defense Language Proficiency Test (DLPT) and/or Oral Proficiency Interview at the education/test center. Pay depends on the language list and your score; recertify yearly.",
        "Book a DLPT/OPI for Haitian Creole and French at the education center: a passing score means extra pay every month."),
    "special_pays": ("Pay", "Special and incentive pays", 2, ALWAYS,
        "Depending on your job and assignment: parachute (jump) pay on jump status, hardship duty pay, special duty assignment pay, hazardous duty pay and more.",
        "Ask S1/finance which apply to your MOS and unit; verify on your LES each month.",
        "If you're on jump status, check parachute duty pay is on your LES."),
    "savings_deposit": ("Money", "Savings Deposit Program (deployments)", 2, lambda p: _yes(p.get("deployed")),
        "Deployed to a qualifying combat zone, you can deposit up to $10,000 and earn 10% annual interest, set by law.",
        "Enroll through finance while deployed (allotment or cash).",
        "You're deployed: enroll in the Savings Deposit Program for the 10% interest."),
    "combat_tax": ("Money", "Combat Zone Tax Exclusion", 2, lambda p: _yes(p.get("deployed")),
        "Pay earned in a designated combat zone is excluded from federal income tax (fully for enlisted).",
        "Automatic in a designated area; check your LES and W-2.",
        "Check the exclusion on your LES and W-2."),
    "miltax": ("Money", "MilTax: free tax filing", 2, ALWAYS,
        "Free tax software and tax experts for service members and families through Military OneSource; most posts also run a free tax center in tax season.",
        "militaryonesource.mil/MilTax, or the installation tax center (legal office) Jan-Apr.",
        "File taxes free with MilTax or the post tax center instead of paying a preparer."),
    "scra": ("Money", "SCRA protections", 2, ALWAYS,
        "The Servicemembers Civil Relief Act caps interest at 6% on debts from before active duty, lets you break a home or car lease for PCS or deployment orders, and protects against default judgments. It also lets a military spouse keep the same state of legal residence for taxes.",
        "Send the lender a written request with your orders; JAG legal assistance helps for free.",
        "Any loans from before you joined? Ask the lenders for the SCRA 6% cap."),
    "aer": ("Money", "Army Emergency Relief", 3, ALWAYS,
        "Zero-interest loans and grants for emergencies (car repair, emergency travel, rent, PCS costs), plus scholarships for spouses and children.",
        "Through your chain of command or the AER office at ACS; armyemergencyrelief.org.",
        "Remember AER before ever taking a payday loan."),
    "financial_counseling": ("Money", "Free financial counseling", 2, ALWAYS,
        "Accredited personal financial counselors for budgeting, debt, car and home buying, and TSP: free and confidential.",
        "Army Community Service (ACS) Financial Readiness, or Military OneSource 800-342-9647.",
        "Book one free session to review your budget and TSP."),
    # -- health and insurance ----------------------------------------------------------------------
    "tricare_family": ("Health", "TRICARE for your spouse", 1, MARRIED,
        "Your spouse gets TRICARE (Prime: almost no out-of-pocket at military clinics; Select: more choice). She must be in DEERS first; changes must be made within 90 days of a life event like marriage or a PCS.",
        "DEERS at the ID card office, then tricare.mil or the regional contractor.",
        "Make sure your spouse is enrolled in TRICARE Prime or Select, not just in DEERS."),
    "dental_family": ("Health", "TRICARE Dental Program for family", 2, MARRIED,
        "Soldiers get dental care at military clinics free; family members need the TRICARE Dental Program (low monthly premium, cleanings covered).",
        "Enroll at tricare.mil/dental; the premium comes out of your pay.",
        "Enroll your spouse in the TRICARE Dental Program."),
    "sgli": ("Health", "SGLI / FSGLI life insurance", 1, ALWAYS,
        "Servicemembers' Group Life Insurance covers you up to the maximum by default at a very low premium; Family SGLI covers your spouse; TSGLI pays for traumatic injuries.",
        "Check and update beneficiaries in milConnect (SOES).",
        "Check your SGLI beneficiaries in milConnect, especially after marriage."),
    "counseling": ("Health", "Free confidential counseling", 3, ALWAYS,
        "Military OneSource and Military & Family Life Counselors offer free, confidential, non-medical counseling for you and your spouse, not reported to the command.",
        "militaryonesource.mil or 800-342-9647, 24/7; MFLCs at your unit or ACS.", ""),
    "parental_leave": ("Family", "Parental leave", 2, ALWAYS,
        "Both parents in the military get 12 weeks of parental leave for a birth, adoption or long-term foster placement.",
        "Request through your chain of command; plan dates with S1 before the birth.",
        "Expecting? Plan the 12 weeks of parental leave with S1 early."),
    # -- education and career ----------------------------------------------------------------------
    "tuition_assistance": ("Education", "Army Tuition Assistance", 1, ALWAYS,
        "The Army pays college tuition while you serve, up to a per-credit-hour and yearly cap (historically $250 per semester hour and $4,000 a year; confirm this year's caps). It doesn't use your GI Bill.",
        "Apply on ArmyIgnitED before the class starts; see the education center counselor first.",
        "Take a class on Tuition Assistance: free college that doesn't touch your GI Bill."),
    "credentialing": ("Education", "Army Credentialing Assistance", 1, ALWAYS,
        "Pays for industry certifications and their courses and exams: IT (CompTIA A+, Security+, Cisco CCNA, AWS), trades, project management and more. Shares the yearly cap with Tuition Assistance. A great fit for your homelab and engineering interests.",
        "Apply on ArmyIgnitED; browse eligible credentials on Army COOL (cool.osd.mil/army).",
        "Get an IT certification (Security+, CCNA, AWS) paid by Credentialing Assistance."),
    "gi_bill_transfer": ("Education", "Transfer the Post-9/11 GI Bill to your spouse", 1, MARRIED,
        "You can transfer GI Bill months to your spouse or children, but only while serving, after 6 years of service and agreeing to 4 more. Miss the window and the option is gone.",
        "Request on milConnect (Transfer of Education Benefits); your career counselor confirms the obligation.",
        "Plan the GI Bill transfer: request it while serving, at 6+ years with a 4-year commitment."),
    "gi_bill": ("Education", "Post-9/11 GI Bill", 2, ALWAYS,
        "Tuition, a housing allowance and a books stipend; the percentage grows with active-duty time, reaching 100% at 36 months.",
        "va.gov/education; the education center can help you plan.", ""),
    "mycaa": ("Family", "MyCAA for your spouse", 1,
        lambda p: MARRIED(p) and _grade(p) in {"E1", "E2", "E3", "E4", "E5", "E6", "W1", "W2", "O1", "O2"},
        "My Career Advancement Account gives eligible military spouses up to $4,000 for licenses, certifications or an associate degree in portable careers.",
        "Your spouse applies at mycaa.militaryonesource.mil; free SECO career coaches help her choose (800-342-9647).",
        "Have your spouse open a MyCAA account: up to $4,000 toward a certificate or license."),
    "spouse_employment": ("Family", "Military spouse hiring and license help", 2, MARRIED,
        "Military Spouse Preference for federal jobs, the Military Spouse Employment Partnership, and reimbursement of re-licensing costs after a PCS.",
        "MySECO (myseco.militaryonesource.mil), USAJOBS spouse preference, MSEP job board.", ""),
    "skillbridge": ("Career", "SkillBridge (before you ETS)", 2, NEAR_ETS,
        "In your last 180 days you can do a civilian internship or training program full-time while still getting military pay.",
        "Get command approval early; programs on skillbridge.osd.mil. Pair with the Army Career Skills Program.",
        "ETS within a year: line up a SkillBridge internship and command approval now."),
    "tap": ("Career", "Transition Assistance and VA disability (BDD)", 2, NEAR_ETS,
        "TAP must start at least a year before separation. File a VA disability claim 180-90 days before you get out (Benefits Delivery at Discharge) so ratings start right away.",
        "Installation Transition Center; va.gov BDD.",
        "Start TAP now and plan your BDD claim 180-90 days before ETS."),
    # -- housing, family, travel -------------------------------------------------------------------
    "va_loan": ("Housing", "VA home loan", 2, ALWAYS,
        "Buy a home with no down payment and no private mortgage insurance, usually at better rates. Eligible after 90 continuous days of active duty; there's a one-time funding fee.",
        "Get a Certificate of Eligibility on va.gov, then compare VA lenders. See an ACS financial counselor first.", ""),
    "pcs_money": ("Moving", "PCS entitlements", 1, lambda p: _yes(p.get("pcs_soon")),
        "Dislocation Allowance, Temporary Lodging Expense, per diem and mileage, and a Personally Procured Move (move your own things and get paid most of what the government would have spent).",
        "Transportation office and move.mil as soon as you have orders; keep weight tickets and receipts.",
        "PCS coming up: ask the transportation office about a Personally Procured Move and claim DLA and TLE."),
    "fsa": ("Family", "Family Separation Allowance", 2, lambda p: MARRIED(p) and _yes(p.get("deployed")),
        "A monthly allowance when you're away from your family more than 30 days (deployment, long TDY, unaccompanied tour).",
        "Submit the DD 1561 to finance.",
        "Away 30+ days: make sure Family Separation Allowance started."),
    "commissary": ("Shopping", "Commissary and Exchange", 1, ALWAYS,
        "The commissary sells groceries at cost plus a 5% surcharge, often 20%+ cheaper than off-post stores, and the Exchange is tax-free. Big savings for your grocery budget.",
        "Shop on post or use commissary click-and-go; the MILITARY STAR card has no annual fee.",
        "Do the big grocery run at the commissary to stretch the grocery budget."),
    "legal": ("Family", "Free legal help (JAG)", 2, ALWAYS,
        "Wills, powers of attorney, notary, landlord and consumer problems, SCRA letters and tax help, all free at the legal assistance office.",
        "Installation legal assistance office (walk-in hours for POAs and notary).",
        "Get a free will and powers of attorney done, especially before deployment or field time."),
    "childcare": ("Family", "Child care and fee assistance", 2, lambda p: _num(p.get("children")) > 0,
        "On-post Child Development Centers charge by family income; off-post fee assistance helps when there's no CDC space.",
        "Request care on militarychildcare.com.", ""),
    "leave": ("Time off", "Leave, passes and selling leave", 3, ALWAYS,
        "You earn 30 days of leave a year (2.5 per month) and can usually carry 60 into the new fiscal year (more in special cases); anything above is lost on 1 October. You can sell back up to 60 days over a career.",
        "Track it on your LES; request in IPPS-A.",
        "Check your leave balance so nothing expires on 1 October."),
    "space_a": ("Travel", "Space-A flights and military lodging", 2, ALWAYS,
        "Space-Available flights on military aircraft are free (small taxes on some contract flights). Military hotels and Armed Forces Recreation Centers (like Shades of Green at Disney World) save a lot on trips.",
        "AMC Space-A pages/app; book lodging through the services' lodging sites.",
        "Flexible trip? Check Space-A and military lodging before booking."),
    "mwr_discounts": ("Travel", "MWR, ITT and military discounts", 3, ALWAYS,
        "Free America the Beautiful national parks pass for service members and families, discounted theme-park and event tickets at ITT, and military discounts (airlines' extra free bags, phone plans, home improvement stores).",
        "ITT office on post; get the parks pass at any national park with your ID; verify discounts with ID.me or GovX.",
        "Planning a trip? Grab the free national parks pass and check ITT for discounted tickets."),
    "efmp": ("Family", "Exceptional Family Member Program", 3, lambda p: _yes(p.get("efmp")),
        "Makes sure assignments can support a family member's medical or educational needs.",
        "ACS EFMP office.", ""),
}


def _data() -> dict:
    return storage.load(FILE, {"profile": {"branch": "Army", "component": "Active Duty"}, "benefits": {}})


@tool(
    "Save something about the user's military service for their benefits checklist: rank, pay_grade (E-4...), "
    "years_of_service, married, spouse_name, children, duty_station, ets (YYYY-MM-DD), deployed, airborne, "
    "languages, legal_residence, education_goal, tsp_percent, pcs_soon, lives_on_post, efmp.",
    {"field": {"type": "string", "enum": PROFILE_FIELDS}, "value": {"type": "string"}},
)
def set_military_profile(field: str, value: str) -> str:
    data = _data()
    data["profile"][field] = value.strip()
    storage.save(FILE, data)
    return f"Saved {field.replace('_', ' ')}: {value}."


@tool(
    "Record whether the user is using a benefit (keys from benefits_checkup), so Jarvis stops reminding or keeps "
    "reminding.",
    {"benefit": {"type": "string", "enum": list(BENEFITS)}, "status": {"type": "string", "enum": STATUSES},
     "note": {"type": "string"}},
    required=["benefit", "status"],
)
def set_benefit_status(benefit: str, status: str, note: str = "") -> str:
    data = _data()
    data["benefits"][benefit] = {"status": status, "note": note, "t": date.today().isoformat()}
    storage.save(FILE, data)
    return f"{BENEFITS[benefit][1]}: marked '{status}'."


def _ranked(data: dict) -> list[tuple]:
    p, st = data["profile"], data["benefits"]
    rows = []
    for key, (area, title, prio, applies, what, how, action) in BENEFITS.items():
        status = st.get(key, {}).get("status", "unsure")
        if not applies(p) or status == "not eligible":
            continue
        rows.append((0 if status == "not using" else 1 if status == "unsure" else 2, prio, key, area, title, status, action))
    return sorted(rows)


@tool(
    "Military benefits checkup: what the user should act on next so they use every active-duty benefit they've "
    "earned, personalised from their profile (and which profile details are still missing). Use for 'am I using my "
    "benefits?', 'what am I missing?', and proactively after life events (marriage, baby, PCS, deployment, ETS).",
    {"area": {"type": "string", "description": "Optional: Pay, Money, Health, Education, Family, Housing, Moving, "
                                                "Career, Shopping, Travel, Time off"}},
    required=[],
)
def benefits_checkup(area: str = "") -> str:
    data = _data()
    rows = [r for r in _ranked(data) if not area or r[3].lower() == area.lower()]
    todo = [r for r in rows if r[5] != "using" and r[6]][:8]
    missing = [f for f in ("pay_grade", "years_of_service", "married", "duty_station", "ets", "languages")
               if not str(data["profile"].get(f, "")).strip()]
    lines = []
    if missing:
        lines.append("Profile gaps (ask about these to personalise it): " + ", ".join(missing) + ".")
    lines.append("Act on these next (most valuable first):" if todo else "Everything on the checklist is covered.")
    lines += [f"- [{k}] {t}: {a}" for _, _, k, _, t, _, a in todo]
    using = [t for _, _, _, _, t, s, _ in rows if s == "using"]
    if using:
        lines.append("Already using: " + ", ".join(using) + ".")
    lines.append("Amounts and caps change yearly: confirm current figures with search_web_free or S1/finance.")
    return "\n".join(lines)


@tool("Full details of one benefit: what it is, who qualifies, and exactly how and where to get it.",
      {"benefit": {"type": "string", "enum": list(BENEFITS)}})
def benefit_details(benefit: str) -> str:
    area, title, _, _, what, how, action = BENEFITS[benefit]
    st = _data()["benefits"].get(benefit, {})
    out = f"{title} ({area}). {what}\nHow/where: {how}"
    if action:
        out += f"\nNext step: {action}"
    if st:
        out += f"\nYour status: {st['status']}" + (f" ({st['note']})" if st.get("note") else "")
    return out


@tool("Open the personal military benefits page in the browser: profile, what to do next, and every benefit "
      "by area with its status and how to claim it.")
def military_benefits_page() -> str:
    data = _data()
    p = data["profile"]
    esc = html.escape
    badge = {"using": ("#2e7d32", "USING"), "not using": ("#c62828", "NOT USING"), "unsure": ("#b26b00", "CHECK"),
             "not eligible": ("#555", "N/A")}
    todo = [r for r in _ranked(data) if r[5] != "using" and r[6]][:8]
    prof = ", ".join(f"{k.replace('_', ' ')}: {esc(str(v))}" for k, v in p.items() if str(v).strip())
    body = [f"<p class='muted'>{prof}</p>",
            "<h2>Do these next</h2><ol>" + "".join(f"<li><b>{esc(t)}</b>: {esc(a)}</li>"
                                                   for _, _, _, _, t, _, a in todo) + "</ol>"]
    areas: dict[str, list] = {}
    for key, (area, title, _, applies, what, how, _) in BENEFITS.items():
        if applies(p):
            areas.setdefault(area, []).append((key, title, what, how))
    for area, items in areas.items():
        body.append(f"<h2>{esc(area)}</h2><div class='grid'>")
        for key, title, what, how in items:
            color, label = badge[data["benefits"].get(key, {}).get("status", "unsure")]
            body.append(f"<div class='card'><span class='badge' style='background:{color}'>{label}</span>"
                        f"<h3>{esc(title)}</h3><p>{esc(what)}</p><p class='muted'><b>How:</b> {esc(how)}</p></div>")
        body.append("</div>")
    body.append("<p class='muted'>Rates and caps change every year; confirm current numbers with S1, finance, ACS or "
                "the official sites. Generated " + datetime.now().strftime("%d %b %Y") + ".</p>")
    page = ("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,"
            "initial-scale=1'><title>Military Benefits</title><style>"
            ":root{--bg:#0b0f14;--panel:#121923;--text:#e6edf3;--muted:#8b98a5;--accent:#8bc34a}"
            "body{margin:0;padding:28px 16px;background:var(--bg);color:var(--text);font:15px/1.5 'Segoe UI',system-ui,sans-serif}"
            "main{max-width:1100px;margin:0 auto}.tag{color:var(--accent);letter-spacing:.2em;font-size:12px}"
            "h1{margin:4px 0}h2{color:var(--accent);margin:28px 0 10px;font-size:18px}"
            ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}"
            ".card{background:var(--panel);border:1px solid #1f2a36;border-radius:12px;padding:14px}"
            ".card h3{margin:8px 0 4px;font-size:16px}.muted{color:var(--muted);font-size:13px}"
            ".badge{font-size:11px;font-weight:700;letter-spacing:.08em;padding:2px 8px;border-radius:6px;color:#fff}"
            "li{margin:6px 0}</style></head><body><main><div class='tag'>J.A.R.V.I.S. · ACTIVE DUTY ARMY BENEFITS</div>"
            "<h1>Your benefits</h1>" + "".join(body) + "</main></body></html>")
    path = storage.user_dir() / "military" / "benefits.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    from tools.software import is_admin, launch_as_user

    if is_admin():
        launch_as_user(path.as_uri())  # the browser opens with normal rights, not Jarvis's admin rights
    else:
        webbrowser.open(path.as_uri())
    return "Opened the military benefits page in the browser."
