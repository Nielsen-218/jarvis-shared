"""TODO: one line saying what this computes. Pure standard-library Python; no files, network, processes or dynamic code."""


class SkillError(Exception):
    """A NAMED failure. `code` must be listed under "errors" in skill.json."""

    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code


def run(amount):
    if amount < 0:
        raise SkillError("example_error", "amount cannot be negative")
    return {"result": amount}
