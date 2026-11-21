"""OPTIONAL extra checks written by the package author. UNTRUSTED: it can only veto, never approve."""


def judge(params, output):
    problems = []
    if output["total_cents"] < 0:
        problems.append("negative total")
    if len(output["shares_cents"]) != params["people"]:
        problems.append("wrong number of shares")
    return problems
