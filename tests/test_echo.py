"""Jarvis's own voice coming back from the speakers is never taken as the owner talking (owner 2026-11-07, no headphones)."""
from audio import echo

PLAN = ["Plan: 1) close microsoft edge.", "Here's my plan; nothing has run yet — want me to go ahead, sir?"]
STATS = ["The system is showing a CPU usage of roughly 30.6%, memory usage of 91.4%, and a free disk space of 489 GB."]


def test_his_own_words_are_echo():
    assert echo.is_echo("go ahead", PLAN)                                  # could have approved his own plan
    assert echo.is_echo("Go ahead, sir.", PLAN)
    assert echo.is_echo("Jarvis, go ahead", PLAN)
    assert echo.is_echo("memory usage of 91 4 and a free disk", STATS)     # speech-to-text slips, his order
    assert echo.is_echo("", PLAN) and echo.is_echo("  ...  ", PLAN)        # a level spike with no words


def test_the_owner_talking_over_him_is_not_echo():
    assert not echo.is_echo("stop", PLAN)
    assert not echo.is_echo("wait, close chrome instead", PLAN)
    assert not echo.is_echo("no", PLAN)
    assert not echo.is_echo("yes", PLAN)
    assert not echo.is_echo("open notepad", PLAN)
    assert not echo.is_echo("what's my memory usage now", STATS)
    assert not echo.is_echo("go ahead", [])                                # nothing played: it can only be the owner


def test_a_stop_mixed_into_his_voice_still_gets_through():
    assert not echo.is_echo("memory usage of 91 stop and a free disk", STATS)
    assert echo.is_echo("nothing has run yet", PLAN)                       # 'nothing' is his word, not a command


# --- subtract: the owner's words and Jarvis's in one transcript (data/jarvis.log 2026-11-15..20, speakers, no headphones) ---
import pytest  # noqa: E402

UNDERSTOOD = ["Understood, sir."]
AS_I_SAID = ["As I said, sir: I used search_internet to look up today's news and answer your question about the tool."]
READY = ["Understood, sir: I'm here and ready for your next command."]
ACK = ["Yes, sir?"]
LEARN = ["Nothing was found online recently to save, sir.", 'Ask me to search first, then say "learn this".']
PHOTOS = ["I don't have any photos of my own because I am an AI and do not take pictures like a camera would."]


@pytest.mark.parametrize("heard, spoken, owner", [
    ("Go live. Understood, sir.", UNDERSTOOD, "Go live."),
    ("I need to know, did Apex get a new update? As I said, sir.", AS_I_SAID, "I need to know, did Apex get a new update?"),
    ("And streaming protocol. That's what I was telling you. Understood, sir. Yeah, whatever.", READY,
     "And streaming protocol. That's what I was telling you. Yeah, whatever."),         # an embedded courtesy run too
    ("Yes, sir. Tell me about the weather tomorrow.", ACK, "Tell me about the weather tomorrow."),
    ("Yes, sir. Open intelligence.", ACK, "Open intelligence."),
    ("Yes, sir. Reset.", ACK, "Reset."),
    ("Are you watching? Nothing was found online recently. Ask me to search first.", LEARN, "Are you watching?"),
    ("I don't have any photos of my own, because I am an AI and do not take pictures like a camera. Okay. How much RAM "
     "is free?", PHOTOS, "Okay. How much RAM is free?"),                                # Whisper's slips ("would" lost)
    ("Yes sir tell me the weather", ACK, "Tell me the weather"),                         # no punctuation between them
    ("Stop. Understood, sir.", UNDERSTOOD, "Stop."),
])
def test_his_own_words_are_taken_out_and_the_owners_kept(heard, spoken, owner):
    assert echo.subtract(heard, spoken) == owner


@pytest.mark.parametrize("heard, spoken", [
    ("Yes, sir.", ACK),
    ("Understood, sir.", UNDERSTOOD),
    ("Nothing was found online recently. Ask me to search first.", LEARN),
])
def test_nothing_of_the_owner_left_is_all_echo(heard, spoken):
    assert echo.subtract(heard, spoken) == ""


@pytest.mark.parametrize("heard, spoken", [
    ("Bring World Intelligence to the front.", ["World Intelligence is open.", "Should I bring Pythonw to the front?"]),
    ("Hey Jarvis, start streaming protocol.", ["Understood, sir: I'm here for the streaming protocol, ready to help."]),
    ("And Streaming Protocol.", ["Understood, sir: I'm here for the streaming protocol."]),     # 2 words: his
    ("No. Hey Jarvis. Come on. What's that? Open World Intelligence.", ["Open World Intelligence, sir."]),
    ("Hey, wait sir.", ACK),                                                                     # his stop word
    ("No.", ["No, sir, nothing has run yet."]),
    ("Yes.", ACK),
    ("Okay, I want to see what you're doing. Can I see it while you're working on it?",
     ["I am creating a new humanoid character from scratch now, sir.", "Give me a moment while I work on it."]),
    ("Create a new one from scratch. I want to see what you got.", ["I am creating a new humanoid character from scratch now, sir."]),
    ("What's my memory usage now?", STATS),
    ("Go live. Understood, sir.", []),                                                           # nothing played
])
def test_his_own_sentences_sharing_words_with_jarvis_survive_whole(heard, spoken):
    assert echo.subtract(heard, spoken) == heard


@pytest.mark.parametrize("heard, spoken", [
    # data/jarvis.log 2026-11-15: his answers reuse the words of the question he was asked (4-6 words, not word for word)
    ("Create a new one from scratch. I want to see what you got.",
     ["I need to know which character you want me to rig: is it the model you have open in Blender, or a new one I "
      "should create from scratch?"]),
    ("I'm not in the PC. I'm sitting at it.", ["You're not in the PC, sir; you're sitting at it."]),
])
def test_an_answer_that_reuses_his_words_is_not_taken_for_them(heard, spoken):
    assert echo.subtract(heard, spoken) == heard


def test_without_long_runs_only_his_courtesy_runs_go():
    assert echo.subtract("Nothing was found online recently. Open it.", LEARN, long_runs=False) == \
        "Nothing was found online recently. Open it."
    assert echo.subtract("Yes, sir. Open intelligence.", ACK, long_runs=False) == "Open intelligence."


def test_a_stop_word_he_did_not_say_keeps_its_sentence():
    assert echo.subtract("Memory usage of 91, stop, and a free disk.", STATS) == "Memory usage of 91, stop, and a free disk."


def test_decimals_and_web_addresses_are_not_sentence_ends():
    assert echo.subtract("Set it to 3.5 degrees. Understood, sir.", UNDERSTOOD) == "Set it to 3.5 degrees."
    assert echo.subtract("Open google.com. Understood, sir.", UNDERSTOOD) == "Open google.com."


def test_his_stop_words_survive_even_when_jarvis_said_them_too():
    assert echo.subtract("No, sir.", ["Not found, no, sir."]) == "No, sir."
    assert echo.subtract("Stop, sir. Understood, sir.", ["Stop, sir? Understood, sir."]) == "Stop, sir."
    assert echo.owner_part("No, sir, nothing has run yet.", ["No, sir, nothing has run yet."]) == ""   # his whole line
