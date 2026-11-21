"""The local model's 16k window: trim what matters least, keep the background notes, never let Ollama cut blindly."""
import copy
import re

from core import contextfit
from core.contextfit import fit, tokens

SYSTEM = {"role": "system", "content": "Jarvis instructions. " * 1600}          # ~7.7k tokens, like personality.md
BACKGROUND = "<background>\nOwner lives in Springfield, IL. " + "memory fact. " * 900 + "\n</background>"
TOOLS16 = [{"type": "function", "function": {"name": f"tool_{i}", "description": "does a thing " * 30,
                                             "parameters": {"type": "object", "properties": {}}}} for i in range(16)]


def _exchange(i, tool=False, lookup=True):
    """One question (with its memory lookup), an optional tool round, and the answer."""
    parts = ([{"type": "text", "text": f"<retrieved-memory>\nlookup for question {i}. " + "x " * 300 + "\n</retrieved-memory>"}]
             if lookup else []) + [{"type": "text", "text": f"Question {i}?"}]
    out = [{"role": "user", "content": parts}]
    if tool:
        out += [{"role": "assistant", "content": None, "tool_calls": [{"id": f"c{i}", "type": "function",
                 "function": {"name": "get_weather", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": f"c{i}", "content": f"Result {i}: sunny.\n" + "detail line. " * 60}]
    out.append({"role": "assistant", "content": [{"type": "text", "text": f"Answer {i}. " + "words " * 40}]})
    return out


def _conversation(n, tools_every=2):
    first = _exchange(0)
    first[0]["content"] = [{"type": "text", "text": BACKGROUND}] + first[0]["content"]
    msgs = [SYSTEM] + first
    for i in range(1, n):
        msgs += _exchange(i, tool=(i % tools_every == 0))
    return msgs


def _texts(messages):
    return [contextfit._text(m) for m in messages]


def test_background_is_pinned_to_the_system_message_and_earlier_lookups_go():
    msgs = _conversation(4)
    before = copy.deepcopy(msgs)
    out = fit(msgs)
    assert msgs == before                                              # the caller's list is untouched
    assert out[0]["content"].startswith(SYSTEM["content"]) and "Owner lives in Springfield, IL." in out[0]["content"]
    assert not any("<background>" in t for t in _texts(out[1:]))
    lookups = [t for t in _texts(out) if "<retrieved-memory>" in t]
    assert len(lookups) == 1 and "question 3" in lookups[0]          # only the current question's lookup stays
    assert sum("Question" in t for t in _texts(out)) == 4               # every question keeps its words


def test_while_a_tool_runs_the_current_questions_lookup_stays():
    msgs = _conversation(3) + _exchange(3, tool=True)[:-1]             # question 3 asked, tool result in, no answer yet
    out = fit(msgs)
    assert out[-1]["role"] == "tool"
    assert any("lookup for question 3" in t for t in _texts(out))
    assert not any("lookup for question 2" in t for t in _texts(out))


def test_a_giant_tool_result_is_capped():
    msgs = _conversation(2)
    msgs += [{"role": "user", "content": "Read me that page"},
             {"role": "assistant", "content": None, "tool_calls": [{"id": "w", "type": "function",
              "function": {"name": "read_web_pages", "arguments": "{}"}}]},
             {"role": "tool", "tool_call_id": "w", "content": "page text " * 5000}]
    out = fit(msgs)
    assert len(out[-1]["content"]) < contextfit.RESULT_CAP + 100 and out[-1]["content"].endswith("(cut: too long to keep whole)")


def test_a_long_conversation_fits_and_keeps_what_matters():
    msgs = _conversation(40)
    assert tokens(msgs) > contextfit.CONTEXT                          # Ollama would have cut the top blindly
    out = fit(msgs, TOOLS16)
    assert tokens(out, TOOLS16) <= contextfit.BUDGET
    assert "Owner lives in Springfield, IL." in out[0]["content"]          # the background survives
    assert any("Question 39?" in t for t in _texts(out))
    recap = _texts(out)[1]
    assert recap.startswith(contextfit.RECAP_HEAD)                     # what they said in the dropped exchanges...
    assert "- Question 1?" in recap and "lookup for question 1" not in recap and "Answer 1." not in recap
    assert not any(t.startswith("Answer 1.") for t in _texts(out))     # ...but not the rest of those exchanges
    # no tool result without the call that produced it
    for i, m in enumerate(out):
        if m["role"] == "tool":
            assert any(c["id"] == m["tool_call_id"] for p in out[:i] for c in p.get("tool_calls") or [])


def test_older_tool_results_shrink_before_any_exchange_is_dropped():
    msgs = _conversation(9, tools_every=1)
    out = fit(msgs, TOOLS16)
    results = [m["content"] for m in out if m["role"] == "tool"]
    assert results[-1].startswith("Result 8: sunny.") and "detail line." in results[-1]      # newest: whole
    assert results[-2].startswith("Result 7: sunny.") and "detail line." in results[-2]
    assert results[0].endswith("(older result, shortened to save space)")


def test_trimming_is_the_same_on_small_talk_and_on_requests():
    msgs = _conversation(30)
    assert _texts(fit(msgs, [])) == _texts(fit(msgs, TOOLS16))       # Ollama's cached prompt stays valid


def test_as_the_conversation_grows_the_start_only_moves_forward_in_steps():
    """Each turn reuses the previous prompt (Ollama's cache) unless whole exchanges had to go — and the cut never
    moves back (that made Ollama re-read the conversation nearly every turn)."""
    msgs = _conversation(12)
    recapped, last = [], None
    for i in range(12, 40):
        msgs = msgs + _exchange(i, tool=(i % 2 == 0))
        last = fit(msgs, TOOLS16)
        recapped.append(sum(line.startswith("- ") for line in _texts(last)[1].splitlines()))
        assert tokens(last, TOOLS16) <= contextfit.BUDGET
    assert recapped == sorted(recapped)                                # only forward
    assert sum(a != b for a, b in zip(recapped, recapped[1:])) <= len(recapped) // 2   # and not every turn


def test_both_rounds_of_a_turn_keep_the_same_earlier_conversation():
    """The answer after a tool must reuse the first round's cached prompt: the incoming tool result can't move the cut."""
    msgs = _conversation(30)
    turn = _exchange(30, tool=True)
    first_round = fit(msgs + turn[:1], TOOLS16)                       # the question, tools offered
    second_round = fit(msgs + turn[:3], TOOLS16)                      # + the tool call and its result
    assert _texts(second_round)[:len(first_round)] == _texts(first_round)
    assert tokens(second_round) <= contextfit.BUDGET


def test_the_recap_keeps_their_words_and_stays_bounded():
    chat = _conversation(1)
    chat[1]["content"][-1]["text"] = "Remember the code word: PINEAPPLE-42."
    first = fit(chat + [m for i in range(1, 40) for m in _exchange(i, tool=i % 2 == 0)], TOOLS16)
    assert "- Remember the code word: PINEAPPLE-42." in _texts(first)[1]   # their words outlive the exchange
    wordy = [SYSTEM]
    for i in range(150):
        wordy += [{"role": "user", "content": f"Message {i}: " + "a long thing they said " * 8},
                  {"role": "assistant", "content": "Okay."}]
    recap = _texts(fit(wordy, TOOLS16))[1].split("\n]\n")[0] + "\n]"
    assert tokens([{"content": recap}]) <= contextfit.RECAP_ROOM
    assert "Message 0:" not in recap                                    # very old lines go first once it's full...
    assert recap.splitlines()[-2].startswith("- Message 1")               # ...the newest dropped ones stay


def test_the_app_note_is_not_part_of_the_recap():
    group = [{"role": "user", "content": "[Sent from their Jarvis phone app. Keep replies short.] Turn off the lights"}]
    assert contextfit._said(group) == "- Turn off the lights"


def test_short_conversations_are_untouched_apart_from_the_always_rules():
    msgs = [SYSTEM, {"role": "user", "content": "Thank you."}, {"role": "assistant", "content": "You're welcome, sir."},
            {"role": "user", "content": "How are you?"}]
    assert fit(msgs) == msgs


# -- the running summary (core/convsummary): both sides of the oldest exchanges, folded a batch at a time --------------
SUMMARY = "- They asked about the weather; Jarvis checked it: sunny.\n- They decided to walk to work."


def _first_question(out):
    """The number of the first exchange still shown word for word."""
    for m in out[1:]:
        for p in contextfit._parts(m):
            found = re.fullmatch(r"Question (\d+)\?", p.get("text", ""))
            if found:
                return int(found.group(1))
    return None


def test_with_a_summary_the_summarized_exchanges_become_one_block():
    msgs = _conversation(10)
    out = fit(msgs, TOOLS16, SUMMARY, 6)
    assert out[0]["content"].startswith(SYSTEM["content"]) and "Owner lives in Springfield, IL." in out[0]["content"]
    first = _texts(out)[1]
    assert first.startswith(contextfit.SUMMARY_HEAD) and "They decided to walk to work." in first
    assert "reference only; answer only their latest message" in first
    assert _first_question(out) == 6                                       # exchanges 0-5 are the summary now...
    assert not any(f"Answer {i}." in t for i in range(6) for t in _texts(out))
    assert [i for i in range(10) if any(f"Question {i}?" in t for t in _texts(out))] == [6, 7, 8, 9]   # ...the rest whole
    assert contextfit.RECAP_HEAD not in "".join(_texts(out))               # nothing else had to go
    assert out[-1]["role"] == "assistant" and "Answer 9." in _texts(out)[-1]   # system + summary + kept + current
    assert tokens(out, TOOLS16) <= contextfit.BUDGET


def test_without_a_summary_the_recap_of_their_words_is_the_fallback():
    msgs = _conversation(40)
    for summary, upto in ((None, 0), (None, 12), ("", 12), (SUMMARY, 0)):  # nothing to show: as before
        out = fit(msgs, TOOLS16, summary, upto)
        assert _texts(out)[1].startswith(contextfit.RECAP_HEAD)
        assert contextfit.SUMMARY_HEAD not in "".join(_texts(out))
        assert _texts(out) == _texts(fit(msgs, TOOLS16))


def test_a_summary_and_a_recap_together_when_even_the_rest_does_not_fit():
    msgs = _conversation(40)
    out = fit(msgs, TOOLS16, SUMMARY, 6)
    first = _texts(out)[1]
    assert first.startswith(contextfit.SUMMARY_HEAD) and contextfit.RECAP_HEAD in first
    assert "- Question 6?" in first and "- Question 5?" not in first       # the recap covers only what came after it
    assert tokens(out, TOOLS16) <= contextfit.BUDGET


def test_dropped_says_when_the_next_turn_would_lose_exchanges():
    assert contextfit.dropped(_conversation(4)) == 0
    long = _conversation(40)
    assert contextfit.dropped(long) > 0
    assert contextfit.dropped(long, SUMMARY, 36) == 0                      # folded: everything left fits again
    assert contextfit.dropped([{"role": "user", "content": "hi"}]) == 0    # not a local-model request


def test_with_a_running_summary_the_cut_stays_monotonic_as_a_long_chat_grows():
    """60 turns, the summarizer folding the next 6 exchanges whenever the next turn would drop some: the start of what
    is shown word for word only moves forward, the summary only ever covers more, and most turns reuse the prompt."""
    msgs = _conversation(1)
    summary, upto, starts, uptos = None, 0, [], []
    for i in range(1, 61):
        msgs = msgs + _exchange(i, tool=(i % 2 == 0))
        out = fit(msgs, TOOLS16, summary, upto)
        assert tokens(out, TOOLS16) <= contextfit.BUDGET
        starts.append(_first_question(out))
        uptos.append(upto)
        if summary:
            assert _texts(out)[1].startswith(contextfit.SUMMARY_HEAD)
        # after the turn (core/convsummary): fold the next batch when the next turn would drop exchanges
        done = len(contextfit.exchanges(msgs[1:]))
        if contextfit.dropped(msgs, summary, upto) and done - upto - 2 >= 6:
            upto += 6
            summary = f"- Notes on exchanges 0 to {upto - 1}."
    assert upto >= 6
    assert starts == sorted(starts) and uptos == sorted(uptos)
    assert sum(a != b for a, b in zip(starts, starts[1:])) <= len(starts) // 2
