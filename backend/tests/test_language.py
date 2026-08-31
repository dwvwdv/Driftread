from rss_parser import ParsedArticle, ParsedFeed
from services.language import (
    detect_feed_language,
    normalize_language_code,
    resolve_feed_language,
)


def _feed(text: str, language: str | None = None) -> ParsedFeed:
    return ParsedFeed(
        title="Example",
        url="https://example.com",
        language=language,
        articles=[ParsedArticle(title=text, url="https://example.com/1", summary=text)],
    )


def test_normalize_language_code_collapses_locale_and_aliases():
    assert normalize_language_code(" EN_us ") == "en"
    assert normalize_language_code("zh-Hant-TW") == "zh"
    assert normalize_language_code("eng") == "en"
    assert normalize_language_code("JPN") == "ja"


def test_normalize_language_code_rejects_invalid_values():
    assert normalize_language_code(None) is None
    assert normalize_language_code("") is None
    assert normalize_language_code("english") is None


def test_detector_classifies_clear_english_sample():
    text = (
        "Software engineering teams build reliable systems by testing changes, "
        "reviewing code, monitoring production, and documenting important design "
        "decisions. Good tooling keeps feedback fast and failures understandable. "
    ) * 3
    assert detect_feed_language(_feed(text)) == "en"


def test_detector_classifies_clear_chinese_sample():
    text = (
        "這是一篇關於軟體工程與系統設計的文章，我們會討論如何改善測試流程、"
        "提升程式碼品質、監控服務狀態，並讓團隊更容易理解重要的技術決策。"
    ) * 4
    assert detect_feed_language(_feed(text)) == "zh"


def test_detector_refuses_too_short_sample():
    assert detect_feed_language(_feed("Hello world")) is None


def test_resolution_prefers_existing_manual_value():
    feed = _feed("這是一段足夠長的中文內容，用來確認既有人工分類不會被自動偵測覆寫。" * 4, "zh-TW")
    assert resolve_feed_language(feed, existing="en-US") == "en"


def test_resolution_prefers_publisher_metadata_before_detection():
    feed = _feed("This is deliberately English text and long enough for detection. " * 5, "ja-JP")
    assert resolve_feed_language(feed) == "ja"
