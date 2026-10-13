"""Conservative discovery identities; never collapse feeds by publisher host."""
import logging
import re
from urllib.parse import parse_qs, urlparse, urlunparse

logger = logging.getLogger(__name__)


def feed_url_aliases(url: str) -> tuple[str, ...]:
    """Only conventional feed endpoints get a trailing-slash alias.

    Preserve the actual validated URL for fetching. Query-based feeds and
    arbitrary paths may carry different semantics and must remain distinct.
    """
    parsed = urlparse(url)
    endpoint = parsed.path.rstrip("/").rsplit("/", 1)[-1].lower()
    if parsed.query or parsed.fragment or endpoint not in {"feed", "rss", "atom"}:
        return (url,)
    alternate = (parsed.path.rstrip("/") if parsed.path.endswith("/")
                 else parsed.path + "/")
    return (url, urlunparse(parsed._replace(path=alternate)))


def is_comment_feed(url: str) -> bool:
    """Recognize explicit comment endpoints, not incidental title keywords."""
    parsed = urlparse(url)
    parts = parsed.path.lower().strip("/").split("/")
    query = parse_qs(parsed.query.lower())
    return (
        any(value in {"comments-rss2", "comments-atom", "comments-rss"}
            for value in query.get("feed", []))
        or any(value in {"1", "true", "yes"}
               for value in query.get("withcomments", []))
        or parts[-1:] in (["comments.xml"], ["comments.rss"], ["comments.atom"])
        or (len(parts) >= 2 and parts[-2] == "comments"
            and parts[-1] in {"feed", "rss", "atom", "rss.xml", "atom.xml"})
        or (len(parts) >= 2 and parts[-1] == "comments"
            and parts[-2] in {"feed", "rss", "atom"})
    )


# Content-based identity. The URL rules above cannot see that /feed and /rss
# on the same site, or a FeedBurner proxy, serve the same items; the stored
# articles can. See docs/SOURCE_MODEL.md "收錄前的來源身分檢查".
COMMENT_ITEM_MIN_ITEMS = 3
COMMENT_ITEM_RATIO = 0.8
MIRROR_MIN_ITEMS = 5
MIRROR_MIN_RATIO = 0.8
MIRROR_SAMPLE_LIMIT = 100
_COMMENT_FRAGMENT = re.compile(r"^comment-\d+$", re.IGNORECASE)


def items_look_like_comments(item_urls: list[str]) -> bool:
    """WordPress-style comment feeds link every item to `post#comment-N`."""
    urls = [url for url in item_urls if url]
    if len(urls) < COMMENT_ITEM_MIN_ITEMS:
        return False
    hits = sum(1 for url in urls if _COMMENT_FRAGMENT.match(url.partition("#")[2]))
    return hits >= COMMENT_ITEM_RATIO * len(urls)


def canonical_item_urls(item_urls: list[str]) -> list[str]:
    """Same identity as the articles trigger: the URL without its fragment."""
    seen: dict[str, None] = {}
    for url in item_urls:
        canonical = (url or "").split("#", 1)[0]
        if canonical:
            seen.setdefault(canonical, None)
    return list(seen)[:MIRROR_SAMPLE_LIMIT]


def find_mirror_source(db, item_urls: list[str], exclude_feed_id: str | None = None) -> dict | None:
    """Return an active readable source that already carries these items.

    Both directions must overlap: most new items are already stored by the
    existing source, and most of that source's newest items are in the new
    feed. The second condition keeps a category feed (a subset of the main
    feed) from being mistaken for a mirror of it.
    """
    sample = canonical_item_urls(item_urls)
    if len(sample) < MIRROR_MIN_ITEMS:
        return None
    try:
        rows = db.rpc("find_mirror_sources", {
            "p_canonical_urls": sample,
            "p_exclude_feed_id": exclude_feed_id,
        }).execute()
    except Exception:
        # Fail open: a missed duplicate is cleanable, while a failed lookup
        # must not stop imports or the scheduler (for example while a worker
        # runs ahead of the api applying this migration).
        logger.warning("Mirror source lookup failed", exc_info=True)
        return None
    needed = MIRROR_MIN_RATIO * len(sample)
    for row in (getattr(rows, "data", None) or []):
        if row["overlap"] >= needed and row["recent_overlap"] >= needed:
            return row
    return None


def same_items(a: set[str], b: set[str]) -> bool:
    """Two parsed feeds carry the same items: both judged, overlap both ways."""
    if len(a) < MIRROR_MIN_ITEMS or len(b) < MIRROR_MIN_ITEMS:
        return False
    shared = len(a & b)
    return shared >= MIRROR_MIN_RATIO * len(a) and shared >= MIRROR_MIN_RATIO * len(b)


def new_source_rejection(db, item_urls: list[str], exclude_feed_id: str | None = None) -> tuple[str, dict | None] | None:
    """Shared admission gate for a feed whose items were just parsed.

    Returns ("comment_feed", None), ("mirror", existing_source) or None.
    """
    if items_look_like_comments(item_urls):
        return ("comment_feed", None)
    mirror = find_mirror_source(db, item_urls, exclude_feed_id)
    if mirror:
        return ("mirror", mirror)
    return None
