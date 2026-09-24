import pytest

from speech_to_text import __main__ as cli
from speech_to_text.history import HistoryStore
from speech_to_text.learning import build_profile, correction_pairs, is_term, mine_vocabulary


@pytest.fixture
def store(tmp_path):
    return HistoryStore(tmp_path / "history.db")


def test_add_recent_and_paste_status(store):
    first = store.add(raw_text="um hello", final_text="Hello.", language="en", app_name="Slack")
    second = store.add(raw_text="olá", final_text="Olá.", language="pt", audio_seconds=1.5)
    store.mark_pasted(first, True)
    store.mark_pasted(second, False)

    recent = store.recent()
    assert [t.id for t in recent] == [second, first]
    assert recent[0].pasted == 0 and recent[1].pasted == 1
    assert recent[1].app_name == "Slack"
    assert [t.id for t in store.recent(search="hello")] == [first]
    assert [t.id for t in store.recent(language="pt")] == [second]
    assert store.language_counts() == {"en": 1, "pt": 1}
    assert store.stats()["failed_pastes"] == 1


def test_corrections_accumulate(store):
    tid = store.add(raw_text="push to get hub", final_text="Push to get hub.")
    store.set_correction(tid, "Push to GitHub.", [("get hub", "GitHub")])
    assert store.get(tid).best_text == "Push to GitHub."
    assert store.corrections() == [("get hub", "GitHub", 1)]
    store.add_correction("get hub", "GitHub")
    assert store.corrections(min_count=2) == [("get hub", "GitHub", 2)]
    assert store.corrected_examples() == [("push to get hub", "Push to GitHub.")]
    assert store.remove_correction("GET HUB") == 1
    assert store.corrections() == []


def test_correction_pairs():
    assert correction_pairs("Push it to get hub today.", "Push it to GitHub today.") == [("get hub", "GitHub")]
    assert correction_pairs("Call fetch user.", "Call fetchUser.") == [("fetch user", "fetchUser")]
    assert correction_pairs("Hello world.", "Hello world!") == []  # punctuation only
    # A rewritten sentence isn't a word swap; it teaches through examples instead.
    assert correction_pairs("a b c d e f", "u v w x y z") == []


@pytest.mark.parametrize(
    "word, sentence_start, expected",
    [
        ("Supabase", False, True),
        ("Supabase", True, False),  # can't tell from a sentence-initial capital
        ("fetchUserById", True, True),
        ("GitHub", True, True),
        ("utils.ts", False, True),
        ("max_retry_count", False, True),
        ("OAuth2", False, True),
        ("API", False, True),
        ("hello", False, False),
        ("I'm", False, False),
        ("felipe@example.com", False, False),
    ],
)
def test_is_term(word, sentence_start, expected):
    assert is_term(word, sentence_start) is expected


def test_mine_vocabulary():
    texts = [
        "Deploy the Supabase function. Supabase is fine.",
        "Ask Marina about fetchUserById in utils.ts, then ping Marina.",
        "O deploy do Supabase quebrou no fetchUserById.",
        "The build broke.",
    ]
    vocab = mine_vocabulary(texts)
    assert set(vocab) == {"Supabase", "Marina", "fetchUserById"}
    assert "utils.ts" not in vocab  # seen once
    assert "The" not in vocab and "Deploy" not in vocab


def test_build_profile(store):
    for text, lang in [
        ("Ship the Supabase migration.", "en"),
        ("Roda a migration do Supabase antes do deploy.", "pt"),
        ("Check the Supabase logs.", "en"),
    ]:
        store.add(raw_text=text.lower(), final_text=text, language=lang)
    tid = store.add(raw_text="open a pr on get hub", final_text="Open a PR on get hub.", language="en")
    store.set_correction(tid, "Open a PR on GitHub.", [("get hub", "GitHub")])
    store.add_correction("cube control", "kubectl", weight=2)

    profile = build_profile(store)
    assert profile.vocabulary[:2] == ["kubectl", "GitHub"]  # corrected terms come first
    assert "Supabase" in profile.vocabulary
    assert profile.replacements == {"cube control": "kubectl"}  # "get hub" seen once: hint only
    assert ("get hub", "GitHub") in profile.corrections
    assert profile.examples == [("open a pr on get hub", "Open a PR on GitHub.")]
    assert profile.recent_text["pt"] == "Roda a migration do Supabase antes do deploy."
    assert profile.recent_text["en"].endswith("Open a PR on GitHub.")
    assert profile.language_counts == {"en": 3, "pt": 1}


def test_cli_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("speech_to_text.history.DB_PATH", tmp_path / "h.db")
    monkeypatch.setattr("speech_to_text.__main__.load_config", lambda: None)
    monkeypatch.setattr("shutil.which", lambda _name: None)
    store = HistoryStore(tmp_path / "h.db")
    store.add(raw_text="bom dia", final_text="Bom dia!", language="pt")

    cli.main(["history"])
    assert "Bom dia!" in capsys.readouterr().out
    cli.main(["copy"])
    assert capsys.readouterr().out.strip() == "Bom dia!"
    cli.main(["learn", "get hub", "GitHub"])
    cli.main(["profile"])
    out = capsys.readouterr().out
    assert '"get hub" → "GitHub"  ×2 [auto]' in out and "pt 100%" in out
    cli.main(["unlearn", "get hub"])
    assert "Forgot 1" in capsys.readouterr().out
