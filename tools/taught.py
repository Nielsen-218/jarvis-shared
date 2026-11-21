"""Commands the user teaches Jarvis, for the model's path: "When I say X, reply only with Y".

The common phrasings never reach a model (core/quick.py -> core/commands.py handles them exactly). These skills are
for a teaching sentence worded some other way, so the model has a place to put the rule instead of improvising it
from the chat (live: "blue notebook" -> a fact, then a reminder, after a restart). direct=True: the confirmation is
spoken as written; grounding in core/intelligence.py keeps them to sentences that actually teach or forget one."""
from core import commands
from tools.registry import tool


@tool(
    "Learn a command the person is teaching: when they say an exact short phrase (the trigger), Jarvis must answer "
    "with an exact response, nothing else. Only for 'when/if I say X, reply/say Y' instructions; not for facts "
    "(remember), reminders (set_reminder) or corrections (learn_lesson). Teaching the same trigger again updates it.",
    {
        "trigger": {"type": "string", "description": "the exact phrase they will say, e.g. 'blue notebook'"},
        "response": {"type": "string", "description": "exactly what to answer, e.g. 'noted'"},
    },
    direct=True,
)
def learn_command(trigger: str, response: str) -> str:
    rule = commands.teach(trigger, response)
    verb = "Updated" if rule["updated_existing"] else "Learned"
    return f"{verb}, sir: when you say '{rule['trigger']}', I'll say '{rule['response']}'."


@tool(
    "Delete a command the person taught earlier (a trigger phrase and its fixed response) when they ask to forget, "
    "remove or stop that command. Not for memories, notes or reminders.",
    {"trigger": {"type": "string", "description": "the trigger phrase of the command to drop"}},
    direct=True,
)
def forget_command(trigger: str) -> str:
    rule = commands.forget(trigger)
    if rule:
        return f"Forgotten, sir: I no longer answer '{rule['trigger']}'."
    return f"I don't have a command for '{commands._clean(trigger, commands.MAX_TRIGGER)}', sir."


@tool("List the commands the person taught Jarvis (trigger phrases and their fixed responses), when they ask what "
      "they taught him or which commands he knows.", {}, direct=True)
def list_commands() -> str:
    return commands.describe()
