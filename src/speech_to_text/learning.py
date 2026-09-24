"""Learning how you talk, from your own dictation history. All local, no training runs.

What it learns, and where it's used:
- Vocabulary: names/identifiers you keep saying (Supabase, fetchUserById, utils.ts) -> Whisper's
  prompt (so it spells them right) and the cleanup model's prompt.
- Corrections: word fixes you make via "Fix last transcription". Shown to the cleanup model as
  hints right away; applied as automatic replacements once you've made the same fix twice.
- Style: your corrected examples become few-shot examples for the cleanup model, and the tail of
  your recent dictations (per language) primes Whisper to punctuate and phrase things like you do.
- Languages: which of your languages you use most.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .history import HistoryStore, Transcription

_EDGE_PUNCTUATION = ".,;:!?()[]{}\"'“”‘’«»…"


@dataclass
class Profile:
    vocabulary: list[str] = field(default_factory=list)
    replacements: dict[str, str] = field(default_factory=dict)
    corrections: list[tuple[str, str]] = field(default_factory=list)
    examples: list[tuple[str, str]] = field(default_factory=list)
    recent_text: dict[str, str] = field(default_factory=dict)
    language_counts: dict[str, int] = field(default_factory=dict)


def build_profile(store: HistoryStore, history_window: int = 500) -> Profile:
    history = store.recent(limit=history_window, include_unusable=False)  # skip noise and failures
    corrections = store.corrections()

    vocabulary: list[str] = []
    for _wrong, right, _count in corrections:
        if right not in vocabulary:  # anything you bothered to correct is worth spelling right
            vocabulary.append(right)
    for term in mine_vocabulary([t.best_text for t in history]):
        if term not in vocabulary:
            vocabulary.append(term)

    replacements: dict[str, str] = {}
    for wrong, right, count in corrections:  # ordered by count: the most frequent fix wins
        if count >= 2:
            replacements.setdefault(wrong, right)

    return Profile(
        vocabulary=vocabulary[:60],
        replacements=replacements,
        corrections=[(wrong, right) for wrong, right, _ in corrections[:30]],
        examples=store.corrected_examples(limit=3),
        recent_text={lang: recent_style_text(history, lang) for lang in {t.language for t in history if t.language}},
        language_counts=store.language_counts(),
    )


def is_term(word: str, sentence_start: bool) -> bool:
    """Does this look like a name/identifier worth teaching Whisper, rather than an ordinary word?"""
    if len(word) < 2 or "@" in word or "://" in word or word.startswith("I'"):
        return False
    if re.search(r"\d", word) and re.search(r"[A-Za-z]", word):  # v2, S3, OAuth2
        return True
    if "_" in word or re.search(r"\w[./]\w", word):  # snake_case, utils.ts, feature/login
        return len(word) >= 4
    if re.search(r"[A-Z]", word[1:]):  # camelCase, GitHub, API
        return True
    return word[0].isupper() and not sentence_start  # proper noun mid-sentence: Supabase, Felipe


def mine_vocabulary(texts: list[str], min_count: int = 2, limit: int = 40) -> list[str]:
    counts: Counter[str] = Counter()
    for text in texts:
        sentence_start = True
        for token in text.split():
            word = token.strip(_EDGE_PUNCTUATION)
            if word and is_term(word, sentence_start):
                counts[word] += 1
            sentence_start = token.endswith((".", "!", "?", ":", "\n"))
    return [word for word, count in counts.most_common() if count >= min_count][:limit]


def correction_pairs(before: str, after: str, max_words: int = 3) -> list[tuple[str, str]]:
    """Word-level substitutions between what was pasted and what you corrected it to.

    Only small, local swaps are kept ("get hub" -> "GitHub"); rewritten sentences teach
    through the examples instead, not as replacement rules.
    """
    a = [w.strip(_EDGE_PUNCTUATION) for w in before.split()]
    b = [w.strip(_EDGE_PUNCTUATION) for w in after.split()]
    pairs = []
    for op, i1, i2, j1, j2 in SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op != "replace" or i2 - i1 > max_words or j2 - j1 > max_words:
            continue
        wrong, right = " ".join(a[i1:i2]).strip(), " ".join(b[j1:j2]).strip()
        if wrong and right and wrong != right:
            pairs.append((wrong, right))
    return pairs


def recent_style_text(history: list[Transcription], language: str, max_words: int = 40) -> str:
    """The last few sentences you dictated in this language (history is newest first)."""
    words: list[str] = []
    for item in history:
        if item.language != language:
            continue
        words = item.best_text.split() + words
        if len(words) >= max_words:
            break
    return " ".join(words[-max_words:])
