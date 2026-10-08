"""Conservative discovery identities; never collapse feeds by publisher host."""
from urllib.parse import parse_qs, urlparse, urlunparse


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
