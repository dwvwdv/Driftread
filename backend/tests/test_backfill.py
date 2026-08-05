from __future__ import annotations

import backfill


def test_repair_promotes_a_markup_summary_into_content():
    """The row shape the reader's no-content fallback used to choke on: the whole
    article sitting in a text-only field."""
    body = '<p>第一段<a href="https://x">連結</a>。</p><p>第二段</p>'
    summary, content = backfill._repair(body, None)

    assert content == body
    assert summary == "第一段連結。 第二段"


def test_repair_leaves_prose_alone():
    summary, content = backfill._repair("Use <p> for paragraphs", None)
    assert content is None
    assert summary == "Use <p> for paragraphs"


def test_repair_keeps_existing_content():
    summary, content = backfill._repair("<p>blurb</p>", "<p>real body</p>")
    assert content == "<p>real body</p>"
    assert summary == "blurb"


def test_repair_matches_the_parser_on_entities():
    """The reason this is Python and not SQL. Each of these was a separate review
    finding when the backfill tried to restate html.unescape() in PL/pgSQL."""
    # 2231 named entities, not the six a hand-written table had.
    assert backfill._repair("it&rsquo;s &mdash; really&hellip; &copy;2026", None)[0] == (
        "it’s — really… ©2026"
    )
    # Windows-1252 substitutions for the C1 range.
    assert backfill._repair("&#151;dash &#128;euro", None)[0] == "—dash €euro"
    # Hex as well as decimal.
    assert backfill._repair("em&#x2014;dash", None)[0] == "em—dash"
    # Single pass: `&#38;` is `&`, and the `&#8217;` it manufactures must not be
    # decoded again.
    assert backfill._repair("X&#38;#8217; and &#8217;Y", None)[0] == "X&#8217; and ’Y"


def test_repair_is_idempotent():
    """It runs once, but a partially-applied run must not compound if retried."""
    first = backfill._repair('<p>Tom &amp; Jerry &mdash; &#8217;s</p>', None)
    second = backfill._repair(*first)
    assert second == first


def test_repair_handles_empty_and_missing_values():
    assert backfill._repair(None, None) == (None, None)
    assert backfill._repair("", None) == (None, None)
    # A summary of nothing but tags carries no text, so it flattens to NULL —
    # while still being rescued into content, where the markup may yet render.
    assert backfill._repair("<p></p>", None) == (None, "<p></p>")
