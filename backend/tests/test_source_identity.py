from unittest.mock import MagicMock

from services.source_identity import (
    canonical_item_urls,
    find_mirror_source,
    items_look_like_comments,
    new_source_rejection,
)


def _db(rows=None, error=None):
    db = MagicMock()
    if error:
        db.rpc.return_value.execute.side_effect = error
    else:
        db.rpc.return_value.execute.return_value = MagicMock(data=rows or [])
    return db


def _urls(n, prefix="https://example.com/p"):
    return [f"{prefix}{i}" for i in range(n)]


def test_comment_items_need_most_links_to_point_at_comments():
    comments = [f"https://example.com/post/#comment-{i}" for i in range(4)]
    assert items_look_like_comments(comments)
    assert not items_look_like_comments(comments[:2])  # too few to judge
    assert not items_look_like_comments(comments[:2] + _urls(3))
    # A heading anchor or a "#comments" link is an ordinary article link.
    assert not items_look_like_comments([f"https://example.com/{i}#comments" for i in range(4)])


def test_canonical_item_urls_drop_fragments_and_duplicates():
    urls = ["https://a.example/1#x", "https://a.example/1", "", "https://a.example/2"]
    assert canonical_item_urls(urls) == ["https://a.example/1", "https://a.example/2"]
    assert len(canonical_item_urls(_urls(150))) == 100


def test_mirror_needs_overlap_in_both_directions():
    row = {"feed_id": "f1", "feed_url": "https://example.com/feed", "overlap": 10, "recent_overlap": 10}
    db = _db([row])
    assert find_mirror_source(db, _urls(10), exclude_feed_id="self") == row
    args = db.rpc.call_args.args
    assert args[0] == "find_mirror_sources"
    assert args[1] == {"p_canonical_urls": _urls(10), "p_exclude_feed_id": "self"}
    # A category feed: all of its items are in the main feed, but most of the
    # main feed's newest items are not in it.
    assert find_mirror_source(_db([{**row, "recent_overlap": 3}]), _urls(10)) is None
    assert find_mirror_source(_db([{**row, "overlap": 5}]), _urls(10)) is None


def test_mirror_check_skips_tiny_feeds_and_fails_open():
    db = _db()
    assert find_mirror_source(db, _urls(4)) is None
    db.rpc.assert_not_called()
    assert find_mirror_source(_db(error=RuntimeError("PGRST202")), _urls(10)) is None


def test_new_source_rejection_reports_comment_feed_before_querying():
    db = _db()
    comments = [f"https://example.com/post/#comment-{i}" for i in range(10)]
    assert new_source_rejection(db, comments) == ("comment_feed", None)
    db.rpc.assert_not_called()
    assert new_source_rejection(_db(), _urls(10)) is None


def test_screen_new_source_leaves_known_urls_and_vanished_mirrors_alone():
    from services.source_visibility import screen_new_source

    parsed = MagicMock(articles=[MagicMock(url=u) for u in _urls(10)])
    known = MagicMock()
    known.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(data={"id": "x"})
    assert screen_new_source(known, "https://example.com/feed", parsed) is None
    known.rpc.assert_not_called()

    fresh = MagicMock()
    fresh.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(data=None)
    assert screen_new_source(fresh, "https://example.com/comments/feed/", parsed) == ("comment_feed", None)

    row = {"feed_id": "f1", "feed_url": "u", "overlap": 10, "recent_overlap": 10}
    fresh.rpc.return_value.execute.return_value = MagicMock(data=[row])
    # The mirror was archived or made private between the lookup and the read.
    (fresh.table.return_value.select.return_value.eq.return_value.eq.return_value
     .is_.return_value.maybe_single.return_value.execute.return_value) = MagicMock(data=None)
    assert screen_new_source(fresh, "https://example.com/rss", parsed) is None
