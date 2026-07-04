"""Vibe/mood tagging — honest keyword+genre heuristic, not a fabricated classifier.

``vibe_tags()`` scans a film's TMDB keyword names (lowercased) for
substrings mapping to a small fixed vocabulary, OR's in a genre-based
signal, and returns the union. This is real but still NECESSARILY
PARTIAL coverage — a proper classifier would need labelled training data
we don't have, and this module isn't going to fabricate one.

**2026-07-04 update:** measured against the live 5,124-film corpus, the
keyword-only version tagged almost nothing outside `quick_watch` —
`slow_burn` 0.1%, `feel_good` 0.2%, `sad` 2.7%, `tense` 6.8%, `dark` 2.3%
of films got a tag at all (TMDB's keyword tagging is simply too sparse to
carry this alone). Folding in genre as a second, coarser signal (the
household's own suggestion — "make genre part of this") is a real,
inspectable heuristic improvement, not a model: certain genres correlate
strongly enough with a vibe that using them as an OR'd-in signal is
defensible (a Horror film being "tense" or "dark" is a reasonable prior
even with zero matching keywords), and it costs nothing since genres are
already hydrated for every film. It trades some precision for a lot more
recall — a film can still end up over-tagged for a genre that doesn't fit
its specific vibe, but zero-coverage was strictly worse for a filter
users actually rely on.

``quick_watch`` is the one exception to all of the above: it's derived
OBJECTIVELY from ``runtime_min <= 95``, not from keywords or genre,
because runtime is exact and always available (when hydrated).
"""
from __future__ import annotations

# tag -> substrings to look for in lowercased keyword names. Any substring
# match on any keyword tags the film with that vibe.
_VIBE_KEYWORD_SUBSTRINGS: dict[str, list[str]] = {
    "slow_burn": ["slow burn", "slow-paced", "atmospheric"],
    "feel_good": ["feel-good", "feel good", "uplifting", "heartwarming"],
    "sad": ["tearjerker", "melancholy", "tragedy", "grief"],
    "tense": ["suspense", "psychological thriller", "tense"],
    "dark": ["dark comedy", "bleak", "nihilis"],  # "nihilis" catches nihilism/nihilistic
}

# tag -> TMDB genre names that plausibly signal that vibe on their own,
# OR'd in alongside the keyword match above. Deliberately coarse (a
# handful of genres per tag, not an attempt to be exhaustive) — this is a
# recall booster for an otherwise near-empty signal, not a precise model.
_VIBE_GENRE_SIGNALS: dict[str, list[str]] = {
    "slow_burn": ["Drama", "Mystery", "War"],
    "feel_good": ["Comedy", "Family", "Animation", "Music"],
    "sad": ["Drama", "War", "History"],
    "tense": ["Thriller", "Mystery", "Crime", "Horror"],
    "dark": ["Horror", "Crime", "Thriller", "War"],
}

QUICK_WATCH_RUNTIME_MAX = 95

# All tags this module can ever produce, keyword/genre-based + the objective one.
ALL_VIBE_TAGS: list[str] = [*_VIBE_KEYWORD_SUBSTRINGS.keys(), "quick_watch"]


def vibe_tags(metadata_json: dict | None, runtime_min: int | None = None) -> list[str]:
    """Return the vibe tags that apply to this film.

    ``runtime_min`` is accepted as a separate optional param (rather than
    only reading metadata_json's "runtime") so callers can pass the more
    authoritative ``Film.runtime_min`` DB column when available; falls back
    to metadata_json's "runtime" field if not given.
    """
    metadata_json = metadata_json or {}
    keywords_block = metadata_json.get("keywords") or {}
    keyword_names = [
        (k.get("name") or "").lower() for k in (keywords_block.get("keywords") or [])
    ]
    genre_names = {g.get("name") for g in (metadata_json.get("genres") or []) if g.get("name")}

    tags: list[str] = []
    for tag, substrings in _VIBE_KEYWORD_SUBSTRINGS.items():
        keyword_hit = any(sub in kw for kw in keyword_names for sub in substrings)
        genre_hit = bool(genre_names & set(_VIBE_GENRE_SIGNALS.get(tag, [])))
        if keyword_hit or genre_hit:
            tags.append(tag)

    effective_runtime = runtime_min if runtime_min is not None else metadata_json.get("runtime")
    if effective_runtime is not None and effective_runtime <= QUICK_WATCH_RUNTIME_MAX:
        tags.append("quick_watch")

    return tags
