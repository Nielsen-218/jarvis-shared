"""Finance, trips/maps/flights, movies and military benefits (network faked)."""
from datetime import date, timedelta

from tools import finance, military, movies, travel


# -- finance -------------------------------------------------------------------------------------
def test_grocery_budget_flow():
    assert "No budgets yet" in finance.budget_status()
    assert "groceries: $0.00 of $600.00 this month" in finance.set_budget("grocery", 600, "monthly")
    out = finance.log_expense(84.2, "groceries", "commissary")
    assert "Logged $84.20 (groceries, commissary)" in out and "$515.80 left" in out
    out = finance.log_expense(420, "food")  # "food" counts as groceries
    assert "80%+ used" in out
    assert "OVER BUDGET" in finance.log_expense(150, "groceries")
    assert "no gas budget yet" in finance.log_expense(40, "gas")
    summary = finance.spending_summary("this month")
    assert "groceries: $654.20 (budget $600/month)" in summary and "Biggest: $420.00 groceries" in summary
    assert finance.undo_last_expense() == "Removed $40.00 (gas)."
    assert "$-54.20 left" in finance.budget_status("groceries")


# -- trips / maps / flights ----------------------------------------------------------------------
def test_trip_plan():
    soon = (date.today() + timedelta(days=10)).isoformat()
    travel.plan_trip("Miami Christmas", "Miami, FL", soon, soon)
    travel.trip_add("Miami Christmas", "flight", "FAY-MIA Delta 8:05am", 289)
    travel.trip_add("Miami Christmas", "packing", "swim trunks")
    s = travel.trip_summary("miami christmas")
    assert "10 days to go." in s and "Flight: FAY-MIA Delta 8:05am ($289)" in s and "Total so far: $289.00" in s
    assert "Miami Christmas" in travel.list_trips()


def test_flight_search_links():
    out = travel.flight_search("FAY", "MIA", "2026-12-20", "2026-12-27", 2)
    assert "google.com/travel/flights?q=Flights%20to%20MIA%20from%20FAY" in out
    assert "kayak.com/flights/FAY-MIA/2026-12-20/2026-12-27/2adults" in out
    assert "skyscanner.com/transport/flights/fay/mia/261220/261227/" in out and "never book" in out


def test_directions(monkeypatch):
    places = {"Fort Bragg": [{"lat": "35.14", "lon": "-79.0", "display_name": "Fort Bragg, NC"}],
              "Miami": [{"lat": "25.76", "lon": "-80.19", "display_name": "Miami, FL"}]}
    monkeypatch.setattr(travel, "geocode", lambda q, limit=1: places.get(q, []))
    monkeypatch.setattr(travel, "_get", lambda url, timeout=15: {"code": "Ok", "routes": [{"distance": 1_175_000, "duration": 39_600}]})
    out = travel.directions("Fort Bragg", "Miami")
    assert out.startswith("730 miles, about 11 h 0 min driving") and "google.com/maps/dir/" in out
    assert "Couldn't place the start" in travel.directions("Nowhere", "Miami")


# -- movies --------------------------------------------------------------------------------------
def test_movies_fall_back_to_web_search(monkeypatch):
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    monkeypatch.setattr(movies, "_web", lambda q, o: f"WEB[{q}]")
    assert movies.movies_out("in theaters") == "WEB[movies in theaters this week box office]"


def test_movies_with_tmdb(monkeypatch):
    monkeypatch.setenv("TMDB_API_KEY", "x")
    monkeypatch.setattr(movies, "_tmdb", lambda path, **p: {"results": [
        {"title": "Small", "release_date": "2026-10-01", "vote_average": 6.1, "popularity": 1, "overview": "meh"},
        {"title": "Big", "release_date": "2026-10-09", "vote_average": 8.2, "popularity": 99, "overview": "great"}]})
    out = movies.movies_out("in theaters").splitlines()
    assert out[1].startswith("- Big (2026-10-09), rated 8.2/10")


# -- military benefits ---------------------------------------------------------------------------
def _all_areas() -> str:
    return "".join(military.benefits_checkup(a) for a in
                   ("Pay", "Money", "Health", "Family", "Education", "Career", "Shopping", "Travel"))


def test_benefits_personalised():
    out = military.benefits_checkup()
    assert "Profile gaps" in out and "[mycaa]" not in _all_areas()  # not married yet
    for f, v in [("pay_grade", "E-4"), ("married", "yes"), ("languages", "Haitian Creole, French"),
                 ("years_of_service", "3"), ("duty_station", "Fort Bragg"),
                 ("ets", (date.today() + timedelta(days=200)).isoformat())]:
        military.set_military_profile(f, v)
    assert "Profile gaps" not in military.benefits_checkup()
    everything = _all_areas()
    for key in ("bah_dependents", "tricare_family", "flpp", "mycaa", "tsp_match", "skillbridge"):
        assert f"[{key}]" in everything, key
    military.set_benefit_status("tsp_match", "using", "5% Roth")
    assert "Already using: TSP match" in military.benefits_checkup() and "[tsp_match]" not in _all_areas()
    assert "Your status: using (5% Roth)" in military.benefit_details("tsp_match")


def test_benefits_page(monkeypatch):
    opened = []
    monkeypatch.setattr(military.webbrowser, "open", opened.append)
    import tools.software as sw
    monkeypatch.setattr(sw, "is_admin", lambda: False)
    military.set_military_profile("married", "yes")
    assert military.military_benefits_page() == "Opened the military benefits page in the browser."
    page = (military.storage.user_dir() / "military" / "benefits.html").read_text(encoding="utf-8")
    assert "ACTIVE DUTY ARMY BENEFITS" in page and "TRICARE for your spouse" in page and opened
