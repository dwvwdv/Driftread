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

-- Same conservative tag shape the parser uses: the `<` must be followed by a
-- letter or `/`, so a plain-text summary containing "if x < 3 and y > 2" is not
-- mistaken for markup. Postgres AREs are newline-insensitive by default, so `.`
-- already spans the line breaks inside a multi-line tag.

-- 1. Rescue the body. A markup summary on a row with no content *is* the content.
UPDATE articles
SET content = summary
WHERE coalesce(content, '') = ''
  AND summary ~ '<!--|</?[a-zA-Z][^>]*>';

-- 2. Flatten the summary to readable text.
UPDATE articles
SET summary = nullif(
  btrim(
    regexp_replace(
      -- Entities last and `&amp;` last of all, so `&amp;lt;` decodes to the
      -- literal text `&lt;` rather than being re-read as a tag.
      replace(
        replace(
          replace(
            replace(
              replace(
                replace(
                  regexp_replace(
                    -- Tags that imply a break become a space; inline tags such as
                    -- <a> and <em> sit inside a word and are removed outright, or
                    -- Chinese text comes back peppered with gaps.
                    regexp_replace(
                      regexp_replace(
                        -- Markup source, not prose — drop these whole.
                        regexp_replace(
                          summary,
                          '<(script|style)\y.*?</\1[[:space:]]*>', ' ', 'gi'
                        ),
                        '</?[[:space:]]*(address|article|aside|blockquote|br|dd|div|dl|dt|figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)([[:space:]][^>]*)?/?[[:space:]]*>',
                        ' ', 'gi'
                      ),
                      '<!--.*?-->|</?[a-zA-Z][^>]*>', '', 'g'
                    ),
                    '&#(39|039);', '''', 'g'
                  ),
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
WHERE summary ~ '<!--|</?[a-zA-Z][^>]*>|&[a-zA-Z#][a-zA-Z0-9]*;';
