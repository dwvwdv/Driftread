from __future__ import annotations
from rss_parser import parse_feed

RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <link>https://example.com</link>
    <description>A test feed</description>
    <language>en</language>
    <item>
      <title>Article One</title>
      <link>https://example.com/1</link>
      <description>Summary of article one</description>
      <author>Alice</author>
      <pubDate>Wed, 01 Jan 2025 12:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Article Two</title>
      <link>https://example.com/2</link>
      <description>Summary of article two</description>
    </item>
  </channel>
</rss>"""

ATOM_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Test</title>
  <link href="https://atom.example.com" rel="alternate"/>
  <subtitle>An Atom feed</subtitle>
  <entry>
    <title>Atom Entry</title>
    <link href="https://atom.example.com/entry1" rel="alternate"/>
    <id>https://atom.example.com/entry1</id>
    <author><name>Bob</name></author>
    <published>2025-06-01T10:00:00Z</published>
    <summary>Atom entry summary</summary>
  </entry>
</feed>"""


def test_parse_rss_feed():
    feed = parse_feed(RSS_SAMPLE)
    assert feed.title == "Test Feed"
    assert feed.description == "A test feed"
    assert feed.language == "en"
    assert len(feed.articles) == 2
    assert feed.articles[0].title == "Article One"
    assert feed.articles[0].url == "https://example.com/1"
    assert feed.articles[0].author == "Alice"
    assert feed.articles[0].published_at is not None
    assert feed.articles[1].title == "Article Two"


def test_parse_atom_feed():
    feed = parse_feed(ATOM_SAMPLE)
    assert feed.title == "Atom Test"
    assert feed.description == "An Atom feed"
    assert len(feed.articles) == 1
    assert feed.articles[0].title == "Atom Entry"
    assert feed.articles[0].url == "https://atom.example.com/entry1"
    assert feed.articles[0].author == "Bob"
    assert feed.articles[0].published_at is not None


def test_parse_rss_no_articles():
    xml = """<rss version="2.0"><channel><title>Empty</title><link>https://x.com</link></channel></rss>"""
    feed = parse_feed(xml)
    assert feed.title == "Empty"
    assert feed.articles == []
