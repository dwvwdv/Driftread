from __future__ import annotations
from unittest.mock import patch

import pytest

from services.feed_discovery import (
    DiscoveryError,
    _extract_feed_links,
    _normalize_url,
)


def test_extract_feed_links_finds_alternate():
    html = """<html><head>
      <link rel="alternate" type="application/rss+xml" href="/feed.xml" title="Main"/>
      <link rel="alternate" type="application/atom+xml" href="https://other.example.com/atom"/>
      <link rel="stylesheet" href="/style.css"/>
    </head></html>"""
    out = _extract_feed_links(html, "https://example.com/blog")
    urls = [c.feed_url for c in out]
    assert "https://example.com/feed.xml" in urls
    assert "https://other.example.com/atom" in urls
    assert len(out) == 2


def test_extract_feed_links_empty_when_none():
    html = "<html><head></head></html>"
    assert _extract_feed_links(html, "https://example.com") == []


def test_normalize_url_adds_scheme():
    with patch("services.feed_discovery._is_safe_host", return_value=True):
        assert _normalize_url("example.com") == "https://example.com"


def test_normalize_url_rejects_loopback():
    with pytest.raises(DiscoveryError):
        _normalize_url("http://localhost")


def test_normalize_url_rejects_private():
    with patch(
        "services.feed_discovery._is_safe_host", return_value=False
    ):
        with pytest.raises(DiscoveryError):
            _normalize_url("https://10.0.0.1")


def test_normalize_url_rejects_ftp():
    with pytest.raises(DiscoveryError):
        _normalize_url("ftp://example.com")
