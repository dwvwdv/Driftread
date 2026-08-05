-- Backfill for the content/summary split in rss_parser.py.
--
-- `articles.summary` is a *text* field: the reader's no-content fallback and the
-- bookmark rows both print it through interpolation. The parser used to store
-- whatever a feed put in <description>, which for a great many feeds is the whole
-- article as HTML — so the reader showed `<p>…</p>` on screen as literal tags, and
-- `content` stayed NULL even though the body had arrived.
--
-- The parser fix only reaches rows it upserts again. An article that has already
-- scrolled out of its feed's window never will, so history has to be repaired here.
-- Runs once, tracked by `_migrations` like every other file.
--
-- `</[a-zA-Z]|<[a-zA-Z][^>]*/>` is the same discriminator _MARKUP_RE uses in
-- rss_parser.py: a closing or self-closing tag means the value is a document,
-- while prose that merely quotes a tag ("Use <p> for paragraphs", or a summary
-- reading "if x < 3 and y > 2") has neither and must be left exactly as it is.
-- Deciding on "contains a tag" would delete the very token such a sentence is
-- about. Postgres AREs are newline-insensitive by default, so `.` already spans
-- the line breaks inside a multi-line tag.

-- 1. Rescue the body. A document sitting in `summary` with no content *is* the content.
UPDATE articles
SET content = summary
WHERE coalesce(content, '') = ''
  AND summary ~ '</[a-zA-Z]|<[a-zA-Z][^>]*/>';

-- 2. Strip the tags, documents only.
UPDATE articles
SET summary = regexp_replace(
  regexp_replace(
    -- Markup source, not prose — these two go whole.
    regexp_replace(summary, '<(script|style)\y.*?</\1[[:space:]]*>', ' ', 'gi'),
    -- Tags that imply a break become a space; inline tags such as <a> and <em>
    -- sit inside a word and are removed outright below, or Chinese text comes
    -- back peppered with gaps ("这里记录 开源 。").
    '</?[[:space:]]*(address|article|aside|blockquote|br|dd|div|dl|dt|figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)([[:space:]][^>]*)?/?[[:space:]]*>',
    ' ', 'gi'
  ),
  '<!--.*?-->|</?[a-zA-Z][^>]*>', '', 'g'
)
WHERE summary ~ '</[a-zA-Z]|<[a-zA-Z][^>]*/>';

-- 3. Decode entities and tidy whitespace, on tag-shaped prose as well as on what
--    step 2 just flattened. `&amp;` goes last, so a double-escaped `&amp;lt;`
--    ends up as the visible text `&lt;` instead of being re-read as a tag.
UPDATE articles
SET summary = nullif(
  btrim(
    regexp_replace(
      replace(
        replace(
          replace(
            replace(
              replace(
                replace(
                  regexp_replace(summary, '&#(39|039);', '''', 'g'),
                  '&nbsp;', ' '
                ),
                '&apos;', ''''
              ),
              '&quot;', '"'
            ),
            '&gt;', '>'
          ),
          '&lt;', '<'
        ),
        '&amp;', '&'
      ),
      '[[:space:]]+', ' ', 'g'
    )
  ),
  ''
)
WHERE summary IS NOT NULL
  AND (
    summary ~ '&[a-zA-Z#][a-zA-Z0-9]*;'
    -- The gaps step 2 leaves behind where tags used to be.
    OR summary ~ '[[:space:]]{2}'
    OR summary ~ '^[[:space:]]'
    OR summary ~ '[[:space:]]$'
  );

-- 4. A summary that was nothing but tags (`<p></p>`, a lone <img>) is now the
--    empty string, which step 3's WHERE does not match. Both are falsy to every
--    consumer, but the parser writes NULL, so store NULL here too.
UPDATE articles
SET summary = NULL
WHERE summary = '';
