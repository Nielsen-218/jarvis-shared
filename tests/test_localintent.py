from core import localintent


def test_explicit_local_notes_requests_extract_the_topic():
    assert localintent.notes_query("Search my notes for the dentist appointment.") == "dentist appointment"
    assert localintent.notes_query("Jarvis, what do my notes say about project Orion?") == "project Orion"
    assert localintent.notes_query("Could you find our to-do list for oil change?") == "oil change"


def test_local_notes_route_does_not_capture_writes_or_web_searches():
    assert localintent.notes_query("Add a note about the dentist appointment") is None
    assert localintent.notes_query("Search the internet for dentist appointments") is None
    assert localintent.notes_query("Search my shopping list for milk") is None
