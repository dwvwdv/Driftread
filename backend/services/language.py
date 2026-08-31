"""Feed-level language normalization and conservative automatic detection.

Driftread stores one language per feed because browsing, recommendations and the
reading stream all filter at feed granularity.  Publishers are inconsistent
about the metadata they emit (``en-US``, ``EN_us``, ``eng`` ...), and many omit
it entirely, so this module gives every ingestion path one canonical policy:

1. keep an existing stored/manual language when present;
2. otherwise trust feed metadata;
3. otherwise detect from a bounded sample of feed/article text;
4. return ``None`` when the detector is not confident enough.

The stored value is the ISO-639-1 primary language (``en``, ``zh``, ``ja`` ...),
not a locale.  That keeps filtering/recommendation buckets stable instead of
splitting e.g. ``en``, ``en-US`` and ``en-GB`` into three categories.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from langdetect import DetectorFactory, LangDetectException, detect_langs

if TYPE_CHECKING:
    from rss_parser import ParsedFeed

# langdetect is intentionally deterministic for ambiguous samples in tests and
# production.  We still reject low-confidence results below rather than relying
# on the seeded tie-breaker as a classification signal.
DetectorFactory.seed = 0

_MAX_SAMPLE_CHARS = 12_000
_MIN_LETTER_CHARS = 40
_MIN_CONFIDENCE = 0.85
_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)

# Hangul and kana are exclusive to Korean and Japanese respectively, unlike Han
# ideographs which both Chinese and Japanese use. langdetect's n-gram model is
# trained on short Wikipedia text and is known to confuse Han-only samples
# between Chinese, Japanese and Korean, so script membership is checked first
# as a much stronger, deterministic signal before falling back to it.
_HANGUL_RE = re.compile(r"[\uac00-\ud7a3]")
_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_HAN_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")
_SCRIPT_RATIO = 0.3

# Common ISO-639-2 / legacy values seen in real RSS metadata.  Unknown 3-letter
# values are left alone rather than guessed; the detector itself emits ISO-639-1.
_ALIASES = {
    "chi": "zh",
    "zho": "zh",
    "eng": "en",
    "jpn": "ja",
    "kor": "ko",
    "deu": "de",
    "ger": "de",
    "fra": "fr",
    "fre": "fr",
    "spa": "es",
    "ita": "it",
    "por": "pt",
    "rus": "ru",
    "ukr": "uk",
    "vie": "vi",
    "tha": "th",
    "ind": "id",
}


def normalize_language_code(value: str | None) -> str | None:
    """Normalize feed metadata to a stable language bucket.

    Locale/script suffixes are intentionally collapsed to the primary language:
    ``zh-TW``/``zh-Hant`` -> ``zh`` and ``en_US`` -> ``en``.  Empty/invalid
    values return ``None``.
    """
    if not value:
        return None
    token = value.strip().lower().replace("_", "-")
    if not token:
        return None
    primary = token.split("-", 1)[0]
    primary = _ALIASES.get(primary, primary)
    if not re.fullmatch(r"[a-z]{2,3}", primary):
        return None
    return primary


def _sample_text(feed: "ParsedFeed") -> str:
    chunks: list[str] = []
    for value in (feed.title, feed.description):
        if value:
            chunks.append(value)

    # Recent feed payloads usually contain enough evidence in a handful of
    # items.  Bound both item count and total text so classification cannot turn
    # a large RSS body into an unbounded CPU/memory task.
    for article in feed.articles[:12]:
        if article.title:
            chunks.append(article.title)
        if article.summary:
            chunks.append(article.summary)
        if sum(len(c) for c in chunks) >= _MAX_SAMPLE_CHARS:
            break

    return "\n".join(chunks)[:_MAX_SAMPLE_CHARS]


def _script_language(sample: str, letter_count: int) -> str | None:
    """Disambiguate CJK samples directly from Unicode script, before statistics.

    Kana is checked first since Japanese text mixes it with Han ideographs;
    a pure-Han sample with no kana or hangul defaults to Chinese.
    """
    if len(_KANA_RE.findall(sample)) / letter_count >= _SCRIPT_RATIO:
        return "ja"
    if len(_HANGUL_RE.findall(sample)) / letter_count >= _SCRIPT_RATIO:
        return "ko"
    if len(_HAN_RE.findall(sample)) / letter_count >= _SCRIPT_RATIO:
        return "zh"
    return None


def detect_feed_language(feed: "ParsedFeed") -> str | None:
    """Detect a feed language from cached text, conservatively.

    Short or ambiguous samples stay unclassified.  False negatives are cheaper
    than false positives here: an unknown feed remains visible under "all",
    while a wrong language silently pollutes filters and recommendation signals.
    """
    sample = _sample_text(feed)
    letter_count = len(_LETTER_RE.findall(sample))
    if letter_count < _MIN_LETTER_CHARS:
        return None
    script_lang = _script_language(sample, letter_count)
    if script_lang:
        return script_lang
    try:
        candidates = detect_langs(sample)
    except LangDetectException:
        return None
    if not candidates or candidates[0].prob < _MIN_CONFIDENCE:
        return None
    return normalize_language_code(candidates[0].lang)


def resolve_feed_language(feed: "ParsedFeed", existing: str | None = None) -> str | None:
    """Resolve the value Driftread should persist for a parsed feed."""
    return (
        normalize_language_code(existing)
        or normalize_language_code(feed.language)
        or detect_feed_language(feed)
    )
