"""Replaying the owner's own lines through the final build (2026-11-16): 'Tell me about the weather tomorrow.' was answered with an offer to
look it up; 'Where am I located on my PC?' with 'That's where I see you from here' (he has no camera)."""
import pytest

from core import localask, quick

SAMPLE = ("Springfield, Illinois now: clear, 44°F (7°C), feels like 38°F, wind 8 mph.\n"
          "Today: overcast, high 53°F / low 43°F, 0% chance of rain.\n"
          "Tomorrow: overcast, high 56°F / low 43°F, 20% chance of rain.")


@pytest.fixture
def weather(monkeypatch):
    from tools import registry
    monkeypatch.setattr(registry, "names", lambda: ["get_weather"])
    monkeypatch.setattr(registry, "run", lambda name, args: (SAMPLE, False))


@pytest.mark.parametrize("said", [
    "Tell me about the weather tomorrow.", "What's the weather tomorrow?", "How's the weather tomorrow, sir?", "Give me the forecast for tomorrow.",
    "Will it rain tomorrow?"])
def test_a_weather_question_for_tomorrow_is_answered_from_the_forecast(said, weather):
    assert quick._weather(said) == "Tomorrow in Springfield, Illinois: overcast, a high of 56, a low of 43 and a 20 percent chance of rain, sir."


@pytest.mark.parametrize("said", ["What's the weather?", "How's the weather today?", "What's the weather like outside?", "Is it going to rain today?"])
def test_a_weather_question_for_today_gets_now_and_today(said, weather):
    assert quick._weather(said) == ("Right now it's clear and 44 degrees. Today in Springfield, Illinois: overcast, a high of 53, a low of 43 and a "
                                    "0 percent chance of rain, sir.")


@pytest.mark.parametrize("said", [
    "What's the weather in Miami?", "What's the weather like on Mars?", "Tell me about the weather next week.", "Weather report on Friday",
    "I like the weather.", "What's the weather forecast for the weekend?"])
def test_other_weather_lines_are_left_to_the_model(said, weather):
    assert quick._weather(said) is None


def test_a_failed_forecast_is_left_to_the_model(monkeypatch):
    from tools import registry
    monkeypatch.setattr(registry, "names", lambda: ["get_weather"])
    monkeypatch.setattr(registry, "run", lambda name, args: ("I don't know where home is yet.", False))
    assert quick._weather("What's the weather tomorrow?") is None


@pytest.mark.parametrize("said", [
    "Where am I?", "Where am I located?", "Where are we located right now?", "Hey Jarvis, where am I located?", "What town am I in?",
    "What about from my PC? Where am I located on my PC?", "Well, in what state are we located? Okay, so we are in Springfield. Where am I?",
    "What state am I in?", "Where are we at right now?"])
def test_the_ways_he_asks_where_he_is(said):
    assert localask.asks_where_am_i(said)


@pytest.mark.parametrize("said", ["Where is the nearest pizza?", "Where am I going to put this?", "Where is Bahrain?", "Where are my keys?", "I know where I am."])
def test_other_where_questions_are_not_that(said):
    assert not localask.asks_where_am_i(said)


@pytest.mark.parametrize("said", [
    "What's on my calendar today?", "What's on my calendar?", "Do I have anything on my calendar tomorrow?", "What's my schedule today?",
    "Check my calendar.", "What do I have on my schedule this week?"])
def test_a_calendar_question_goes_to_the_calendar_skill_not_the_model(said):
    # Not connected (the test data folder is empty): the skill itself says so, with how to connect it.
    out = quick._calendar(said)
    assert "isn't connected" in out and "tap Calendar" in out


@pytest.mark.parametrize("said", ["Add a meeting to my calendar.", "Show me the outfit calendar.", "What's on the news?", "Set a reminder for my meeting.",
                                  "My calendar is full.", "Open the calendar app.", "What is today?",
                                  "What do I have in my pantry?", "Am I free to go?", "What's today's date?"])
def test_other_lines_are_not_a_calendar_question(said):
    assert quick._calendar(said) is None


# ---- the second replay of his real lines (2026-11-16 ~03:10) ----------------------------------------------------------------------------
@pytest.mark.parametrize("said", [
    "Make sure I'm not getting hacked because I downloaded something or whatever.", "Am I getting hacked?", "Check if my PC is infected.",
    "Scan my PC for malware.", "Do I have a virus?", "Run a security checkup.", "Hey Jarvis, make sure I'm not hacked."])
def test_a_hacked_question_runs_the_real_checkup(said, monkeypatch):
    from tools import registry
    monkeypatch.setattr(registry, "names", lambda: ["security_checkup"])
    monkeypatch.setattr(registry, "run", lambda name, args: ("Security checkup done - nothing looks out of place.", False))
    assert quick._security(said) == "Security checkup done - nothing looks out of place."


@pytest.mark.parametrize("said", ["How do I hack a wifi?", "Hacking is fun.", "Tell me about malware.", "What is a virus?", "I got hacked last year."])
def test_other_lines_do_not_run_the_checkup(said, monkeypatch):
    from tools import registry
    monkeypatch.setattr(registry, "names", lambda: ["security_checkup"])
    monkeypatch.setattr(registry, "run", lambda name, args: pytest.fail("the checkup must not run"))
    assert quick._security(said) is None


def test_the_laptop_asks_the_pc_for_the_checkup(monkeypatch):
    from tools import registry
    from core import pcfast
    monkeypatch.setattr(registry, "names", lambda: [])
    monkeypatch.setattr(pcfast, "ask_pc", lambda raw: "Security checkup done - the PC is clean.")
    assert quick._security("Am I getting hacked?") == "Security checkup done - the PC is clean."


@pytest.mark.parametrize("said", [
    "So if I purchase credits, is that for you?", "Do I need to buy API credits?", "Jarvis, did you use any API by trying to do that?",
    "Did you use the API?", "Are you using the cloud?", "Did that cost me anything?", "Are you costing me money?"])
def test_a_credits_or_api_question_is_answered_from_the_truth_when_local_only(said, monkeypatch):
    from core import intelligence
    monkeypatch.setattr(intelligence, "local_only", lambda: True)
    answer = quick._credits(said)
    assert answer.startswith("No, sir. I think on local models on your PC") and "switched off" in answer


def test_when_the_cloud_is_on_the_model_answers(monkeypatch):
    from core import intelligence
    monkeypatch.setattr(intelligence, "local_only", lambda: False)
    assert quick._credits("Did you use the API?") is None


@pytest.mark.parametrize("said", ["Did you use the oven?", "Did you use my notes?", "Are you using Chrome?", "Do you need anything?", "Is that for you?"])
def test_other_questions_are_not_about_credits(said, monkeypatch):
    from core import intelligence
    monkeypatch.setattr(intelligence, "local_only", lambda: True)
    assert quick._credits(said) is None


@pytest.mark.parametrize("text", [
    "I never say I run on your PC because I am talking to you now as a person, not as a service waiting for work.",
    "Credits bought through the API console pay for my cloud thinking, sir."])
def test_a_leaked_rule_or_a_cloud_claim_is_not_said_when_local_only(text, monkeypatch):
    from core import intelligence
    monkeypatch.setattr(intelligence, "local_only", lambda: True)
    assert intelligence.chat_unsaid(text)


@pytest.mark.parametrize("text", ["I have checked your recent downloads and activity logs, sir; nothing looks like a hack.", "I've scanned your PC.",
                                  "I have reviewed the logs.", "I've verified it."])
def test_checked_scanned_reviewed_verified_with_no_tool_are_claims(text):
    assert intelligence_claims(text)


def intelligence_claims(text):
    from core import intelligence
    return intelligence.claims_done(text)


# ---- the third replay (2026-11-16 03:40) -----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "I'll give you a fresh code right now: 78, 11, 97.", "Your pairing code is 482913.", "Here is the new code: 4 8 2 9 1 3.",
    "Enter the code 123456 on the welcome screen.", "Your PIN is 1234."])
def test_a_code_with_no_tool_behind_it_is_invented(text):
    from core import intelligence
    assert intelligence.invents_code(text)
    assert intelligence.chat_unsaid(text)


@pytest.mark.parametrize("text", ["Say pair my phone and I'll give you a code.", "The code works once.", "I'll need a code from the app.", "Code review is at 3.",
                                  "The area code is Springfield's."])
def test_other_sentences_with_code_are_fine(text):
    from core import intelligence
    assert not intelligence.invents_code(text)


@pytest.mark.parametrize("said", [
    "Hey Jarvis, can you see my screen?", "Can you see my screen right now?", "Do you see my screen?", "Could you look at my screen", "Are you able to see my screen?"])
def test_can_you_see_my_screen_is_a_question_about_him(said):
    answer = quick._see_screen(said)
    if "able to" in said:
        assert answer is None
    else:
        assert answer.startswith("Yes, sir, when you ask I look at it.")


@pytest.mark.parametrize("said", ["What's on my screen?", "Look at my screen and tell me what you see.", "Take a screenshot.", "Can you see it?"])
def test_a_request_to_look_is_not_answered_by_the_ability_line(said):
    assert quick._see_screen(said) is None


@pytest.mark.parametrize("said", ["Uh, yes, I am on a page here, but it's saying to pair my phone.", "It's asking me to pair my phone.", "It is saying to link my phone."])
def test_a_pairing_prompt_is_answered_with_the_real_way_to_a_code(said):
    assert "pair my phone" in quick._pair_prompt(said) and not any(c.isdigit() for c in quick._pair_prompt(said))


@pytest.mark.parametrize("said", ["Pair my phone.", "How do I pair my phone?", "My phone is paired."])
def test_other_phone_lines_are_not_the_prompt(said):
    assert quick._pair_prompt(said) is None


# ---- the fourth replay (2026-11-16 04:10) ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said", [
    "How do I open PC control to run that?", "How do I think this? I I I can't.", "What do I do with this?", "How can I get this working?",
    "Why is this not installing?"])
def test_a_question_is_not_a_request_to_follow_the_guide_on_screen(said):
    from core import screenguide
    assert not screenguide.asked(said)


@pytest.mark.parametrize("said", ["Follow this.", "Install this.", "Set this up.", "Do the steps on my screen.", "Can you get this working?"])
def test_a_real_request_to_follow_the_guide_still_is_one(said):
    from core import screenguide
    assert screenguide.asked(said)


@pytest.mark.parametrize("text", [
    "I checked the startup tasks and scheduled jobs, sir; nothing is set to launch your files folder automatically.",
    "I've scanned your PC and found nothing.", "I have reviewed the logs.", "I looked through your settings."])
def test_a_first_person_check_in_a_chat_explanation_is_false(text):
    from core import intelligence
    assert intelligence.chat_unsaid(text)            # acts=False: an explanation (depth) turn


@pytest.mark.parametrize("text", ["You checked the logs.", "Checking is easy.", "Have you checked the cable?", "I can check the startup tasks if you like."])
def test_other_sentences_with_checked_are_fine(text):
    from core import intelligence
    assert not intelligence.chat_unsaid(text)


@pytest.mark.parametrize("said", [
    "My computer keeps opening my files folder by itself. Can you tell me why it keeps doing that?", "Why does my browser keep opening by itself?",
    "My PC keeps popping up a window on its own, can you check?"])
def test_a_keeps_opening_by_itself_question_runs_the_checkup_and_says_what_it_cannot_tell(said, monkeypatch):
    from tools import registry
    monkeypatch.setattr(registry, "names", lambda: ["security_checkup"])
    monkeypatch.setattr(registry, "run", lambda name, args: ("Security checkup done - nothing looks out of place.", False))
    answer = quick._security(said)
    assert answer.startswith("Security checkup done") and "doesn't tell me what is opening it" in answer


@pytest.mark.parametrize("said", ["How much money do we have left for you right now to use cloud or whatever?", "How much credit do I have left?", "How much money is left for the API?"])
def test_how_much_is_left_for_the_cloud_is_answered_from_the_truth(said, monkeypatch):
    from core import intelligence
    monkeypatch.setattr(intelligence, "local_only", lambda: True)
    assert quick._credits(said).startswith("No, sir. I think on local models")


@pytest.mark.parametrize("said", ["How come I can't access you when I'm away from home?", "Why can't I reach you?", "Why can't we connect to the phone app?"])
def test_how_come_i_cant_reach_you_is_not_explained_by_a_made_up_network_story(said):
    from core import chatturn
    answer = chatturn.why_cant_you_unknown(said, [{"role": "user", "content": said}])
    assert answer.startswith("I'm not sure why, sir, and I won't guess.") and "laptop server" in answer


# ---- the fifth replay (2026-11-16 05:40) -----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("said", ["No, it's all good. Give me a new code.", "Give me a new pairing code.", "I need a fresh code.", "Can I get a new code?"])
def test_a_new_code_is_made_by_the_real_tool_for_the_owner(said, monkeypatch):
    from core import users
    from tools import registry
    monkeypatch.setattr(users, "get", lambda uid=None: {"role": "owner"})
    monkeypatch.setattr(registry, "names", lambda: ["pair_phone"])
    monkeypatch.setattr(registry, "run", lambda name, args: ("Your pairing code is 123 456. It works once.", False))
    assert quick._new_code(said) == "Your pairing code is 123 456. It works once."


def test_a_new_code_for_anyone_but_the_owner_is_left_to_the_normal_path(monkeypatch):
    from core import users
    from tools import registry
    monkeypatch.setattr(users, "get", lambda uid=None: {"role": "member"})
    monkeypatch.setattr(registry, "names", lambda: ["pair_phone"])
    assert quick._new_code("Give me a new code.") is None


@pytest.mark.parametrize("said", ["Give me a code review.", "What is the area code?", "I need a coat.", "Give me the news."])
def test_other_lines_are_not_a_pairing_code_request(said):
    assert quick._new_code(said) is None


@pytest.mark.parametrize("said", [
    "Jarvis, how much usage are we at for you right now? How much more?", "The cloud budget, how do I do that?", "How many tokens do we have?",
    "How much budget is left for you?"])
def test_usage_and_budget_questions_get_the_local_only_truth(said, monkeypatch):
    from core import intelligence
    monkeypatch.setattr(intelligence, "local_only", lambda: True)
    assert quick._credits(said).startswith("No, sir. I think on local models")


@pytest.mark.parametrize("said", ["The cloud budget, how do I do that?", "How do I do that?", "The thing, how can I do that?"])
def test_a_how_do_i_anywhere_is_not_follow_the_guide(said):
    from core import screenguide
    assert not screenguide.asked(said)


@pytest.mark.parametrize("said", [
    "What was the title of the window you saw?", "Which window did you see?", "Who was that?", "Where did you put it?", "How much did it cost?",
    "Is that the right one?"])
def test_a_question_the_planner_could_not_plan_is_not_a_job_that_failed(said):
    from core import pchelp
    assert pchelp.no_plan(said).startswith("I don't have that, sir, and I won't guess.")


@pytest.mark.parametrize("said", ["Open the window.", "Make it bigger.", "Do the thing."])
def test_a_command_that_could_not_be_planned_keeps_its_own_line(said):
    from core import pchelp
    assert "couldn't work out how to do" in pchelp.no_plan(said)


@pytest.mark.parametrize("said", [
    "So you know it's not morning, then why did you say good morning?", "Why did you say that?", "Why do you keep doing that?",
    "Why would you use that tool?"])
def test_why_did_you_without_a_real_cause_is_not_explained_by_a_made_up_reason(said):
    from core import chatturn
    answer = chatturn.why_cant_you_unknown(said, [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Good morning, sir."},
                                                  {"role": "user", "content": said}])
    assert answer.startswith("I don't have a reason for that, sir, and I won't make one up.")


@pytest.mark.parametrize("said", [
    "No, I'm saying what time will it get cooler today?", "What time will it get colder tonight?", "When does it stop raining?",
    "What time will it warm up?"])
def test_an_hourly_weather_question_is_answered_with_what_he_actually_has(said, weather):
    answer = quick._cooler(said)
    assert answer.startswith("I only have the daily forecast, sir, not the hourly one: a high of 53 and a low of 43 today.")
    assert "can't tell you what time" in answer
