"""Vibe/mood tagging — honest keyword-substring heuristic, not a fabricated classifier.

``vibe_tags()`` scans a film's TMDB keyword names (lowercased) for
substrings mapping to a small fixed vocabulary. This is real but
NECESSARILY PARTIAL coverage: TMDB keyword tagging is inconsistent across
films, so plenty of films that genuinely feel "slow_burn" or "feel_good"
simply won't have a matching keyword string and will get zero tags from
this function. That's an honest limitation of a keyword-substring approach
at this data quality level — not a bug to hide, and not something worth
over-engineering for v0 (a real classifier would need labelled training
data we don't have).

``quick_watch`` is the one exception: it's derived OBJECTIVELY from
``runtime_min <= 95``, not from keywords, because runtime is exact and
always available (when hydrated), unlike the keyword tags which have
partial coverage by construction.
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

QUICK_WATCH_RUNTIME_MAX = 95

# All tags this module can ever produce, keyword-based + the objective one.
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

    tags: list[str] = []
    for tag, substrings in _VIBE_KEYWORD_SUBSTRINGS.items():
        if any(sub in kw for kw in keyword_names for sub in substrings):
            tags.append(tag)

    effective_runtime = runtime_min if runtime_min is not None else metadata_json.get("runtime")
    if effective_runtime is not None and effective_runtime <= QUICK_WATCH_RUNTIME_MAX:
        tags.append("quick_watch")

    return tags
