"""The heads-up before something that cannot be undone, in French and Kreyol (the English one is core/riskwarn.py)."""
import pytest

from core import riskwarn_langs as rl


@pytest.mark.parametrize("said,lang,what,letter", [
    ("Formate le disque D.", "fr", "drive", "D"), ("Efface tout mon ordinateur.", "fr", "drive", ""), ("Jarvis, formate mon disque dur.", "fr", "drive", ""),
    ("Supprime tous mes fichiers.", "fr", "everything", ""), ("Efface tout sur mon PC.", "fr", "everything", ""), ("Vide mon disque.", "fr", "drive", ""),
    ("Fòmate disk D la.", "ht", "drive", "D"), ("Efase tout òdinatè mwen.", "ht", "drive", ""), ("Siprime tout fichye mwen yo.", "ht", "everything", ""),
    ("Efase tout bagay sou òdinatè mwen.", "ht", "everything", ""), ("Fòma disk mwen.", "ht", "drive", "")])
def test_a_drastic_order_is_recognised(said, lang, what, letter):
    assert rl.kind(said, lang) == (what, letter)


@pytest.mark.parametrize("said,lang", [
    ("Comment formater un disque ?", "fr"), ("Pourquoi effacer tout ?", "fr"), ("Efface le fichier test.txt.", "fr"), ("J'ai formaté le disque hier.", "fr"),
    ("Supprime tout dans la liste.", "fr"), ("Ne formate pas le disque.", "fr"), ("Kijan pou m fòmate yon disk?", "ht"), ("Efase fichye a.", "ht"),
    ("Mwen te efase tout la.", "ht"), ("Siprime tout nan lis la.", "ht"), ("Bonjour Jarvis.", "fr"), ("Alo Jarvis.", "ht")])
def test_other_lines_are_not_drastic(said, lang):
    assert rl.kind(said, lang) == (None, "")


def test_the_warning_says_what_it_does_that_the_call_is_his_and_ends_with_the_mark():
    fr = rl.handle("Formate le disque D.", [], "fr", "monsieur")
    assert fr.startswith("Cela effacerait tout sur le disque D définitivement, sans retour possible, monsieur.") and fr.endswith("dites « fais-le quand même ».")
    ht = rl.handle("Fòmate disk D la.", [], "ht", "mesye")
    assert ht.startswith("Sa ap efase tout sa ki sou disk D pou tout tan") and ht.endswith("di « fè l kanmenm ».")


@pytest.mark.parametrize("lang,order,insist,needle", [
    ("fr", "Formate le disque D.", "Fais-le quand même.", "Je ne formate pas de disque d'ici"),
    ("fr", "Formate le disque D.", "Oui, vas-y.", "c'est votre décision"),
    ("ht", "Fòmate disk D la.", "Fè l kanmenm.", "Mwen pa fòma disk soti isit la"),
    ("ht", "Fòmate disk D la.", "Mwen sèten.", "se ou menm ki deside")])
def test_his_do_it_anyway_gets_an_honest_answer_that_nothing_ran(lang, order, insist, needle):
    title = "monsieur" if lang == "fr" else "mesye"
    warning = rl.handle(order, [], lang, title)
    history = [{"role": "user", "content": order}, {"role": "assistant", "content": warning}, {"role": "user", "content": insist}]
    answer = rl.handle(insist, history, lang, title)
    assert needle in answer and ("rien touché" in answer or "pa manyen anyen" in answer)
    again = history + [{"role": "assistant", "content": answer}, {"role": "user", "content": insist}]
    assert needle in rl.handle(insist, again, lang, title)               # a second insist is still answered


@pytest.mark.parametrize("lang,insist", [("fr", "Fais-le quand même."), ("ht", "Fè l kanmenm."), ("fr", "Vas-y."), ("ht", "Ale non.")])
def test_an_insist_with_no_warning_before_it_is_left_alone(lang, insist):
    assert rl.handle(insist, [{"role": "user", "content": "Bonjour"}, {"role": "assistant", "content": "Bonjour, monsieur."}, {"role": "user", "content": insist}],
                     lang, "monsieur") is None


def test_english_and_unknown_languages_are_not_handled_here():
    assert rl.handle("Format the D drive.", [], "en", "sir") is None
    assert rl.handle("Formate le disque D.", [], "es", "señor") is None


def test_the_brain_runs_the_french_and_kreyol_layer_whatever_the_detector_says():
    """Live 2026-11-16: 'Formate le disque D.' reached the model, which said 'C'est fait.' (nothing ran). The layer is not gated on the detected language."""
    import inspect
    from brain import llm
    source = inspect.getsource(llm)
    assert "for lang in (\"fr\", \"ht\"):" in source and "riskwarn_langs.handle(plain" in source
    assert "self._tongue in (\"fr\", \"ht\")" not in source


def test_each_language_finds_its_own_warning_in_the_history_when_both_are_tried():
    order = "Fòmate disk D la."
    fr = rl.handle(order, [], "fr", "monsieur")
    ht = rl.handle(order, [], "ht", "mesye")
    assert fr is None and ht is not None                  # the French layer ignores a Kreyol order; the loop then tries Kreyol
    history = [{"role": "user", "content": order}, {"role": "assistant", "content": ht}, {"role": "user", "content": "Fè l kanmenm."}]
    assert rl.handle("Fè l kanmenm.", history, "fr", "monsieur") is None
    assert "Mwen pa fòma disk soti isit la" in rl.handle("Fè l kanmenm.", history, "ht", "mesye")
