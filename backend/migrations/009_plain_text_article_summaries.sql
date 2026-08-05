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
-- Attribute values may legally contain a raw `>` (`<p title="2 > 1">`), so every
-- pattern here walks quoted runs rather than using a plain `[^>]*` — that form
-- stopped at the inner `>` and left `1">Hi` in the summary. Written as
-- `([^>"'']|"[^"]*"|''[^'']*'')*`: in SQL source a literal `'` is doubled, so
-- that is the regex `([^>"']|"[^"]*"|'[^']*')*`. Mirrors _ATTRS in rss_parser.py.
--
-- The `~*` predicate below is the same discriminator _MARKUP_RE uses in
-- rss_parser.py. Three shapes mean the value is a document: a closing tag, an
-- explicitly self-closed tag, or a void element carrying an attribute (an
-- image-only description is the article on a photo blog). Prose that merely
-- quotes a tag — "Use <p> for paragraphs", "use <br> to break lines", or a
-- summary reading "if x < 3 and y > 2" — has none of them and must be left
-- exactly as it is: deciding on "contains a tag" would delete the very token
-- such a sentence is about. Postgres AREs are newline-insensitive by default,
-- so `.` already spans the line breaks inside a multi-line tag.

-- 1. Rescue the body. A document sitting in `summary` with no content *is* the content.
UPDATE articles
SET content = summary
WHERE coalesce(content, '') = ''
  AND summary ~* '</[a-zA-Z]|<[a-zA-Z]([^>"'']|"[^"]*"|''[^'']*'')*/>|<(area|base|br|col|embed|hr|img|input|link|meta|param|source|track|wbr)\y[^><]*=[^><]*>';

-- 2. Strip the tags, documents only.
UPDATE articles
SET summary = regexp_replace(
  regexp_replace(
    -- Markup source, not prose — these two go whole.
    regexp_replace(summary, '<(script|style)\y([^>"'']|"[^"]*"|''[^'']*'')*>.*?</\1[[:space:]]*>', ' ', 'gi'),
    -- Tags that imply a break become a space; inline tags such as <a> and <em>
    -- sit inside a word and are removed outright below, or Chinese text comes
    -- back peppered with gaps ("这里记录 开源 。").
    '</?[[:space:]]*(address|article|aside|blockquote|br|dd|div|dl|dt|figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)([[:space:]/]([^>"'']|"[^"]*"|''[^'']*'')*)?>',
    ' ', 'gi'
  ),
  '<!--.*?-->|</?[a-zA-Z]([^>"'']|"[^"]*"|''[^'']*'')*>|</?[a-zA-Z][^>]*>', '', 'g'
)
WHERE summary ~* '</[a-zA-Z]|<[a-zA-Z]([^>"'']|"[^"]*"|''[^'']*'')*/>|<(area|base|br|col|embed|hr|img|input|link|meta|param|source|track|wbr)\y[^><]*=[^><]*>';

-- 3. Decode numeric character references (`&#8217;`, `&#x2014;`).
--
-- The parser calls html.unescape(), which handles every one of these; a fixed
-- list of `replace()` calls cannot. Feeds are full of `&#8217;` for a curly
-- apostrophe and `&#8212;`/`&#x2014;` for an em dash, and a row that never gets
-- upserted again would show that source text in the bookmark preview forever.
--
-- Runs before step 4 so the pass matches html.unescape()'s single pass: a
-- double-escaped `&amp;#8217;` has no `&#` for this step to find, and step 4
-- then turns it into the visible text `&#8217;` — exactly what Python produces.
DO $$
DECLARE
  row_id  uuid;
  txt     text;
  ent     text;
  code    int;
  -- Code points for 0x80..0x9F, in order. html.unescape() does not treat a
  -- numeric reference in the C1 range as a control character: per the HTML5
  -- spec it substitutes the Windows-1252 character instead, so `&#151;` is an
  -- em dash and `&#128;` is a euro sign. Older feeds — anything downstream of
  -- Word — emit these constantly, and without the table a migrated row would
  -- hold an invisible control character where a re-upserted one holds `—`.
  -- Generated from Python's html._invalid_charrefs rather than transcribed.
  -- Five slots (0x81, 0x8D, 0x8F, 0x90, 0x9D) map to themselves, as in Python.
  c1_map  CONSTANT int[] := ARRAY[
    8364,  129, 8218,  402, 8222, 8230, 8224, 8225,
     710, 8240,  352, 8249,  338,  141,  381,  143,
     144, 8216, 8217, 8220, 8221, 8226, 8211, 8212,
     732, 8482,  353, 8250,  339,  157,  382,  376
  ];
BEGIN
  FOR row_id, txt IN
    SELECT id, summary FROM articles WHERE summary ~ '&#[xX]?[0-9a-fA-F]{1,7};'
  LOOP
    FOR ent IN
      SELECT DISTINCT m[1]
      FROM regexp_matches(txt, '(&#[xX]?[0-9a-fA-F]{1,7};)', 'g') AS m
    LOOP
      BEGIN
        IF ent ~* '^&#x' THEN
          -- lpad is load-bearing: the text->bit cast pads on the *right*, so
          -- ('x' || '2014')::bit(32) is 0x20140000, not 0x2014.
          code := ('x' || lpad(substring(ent from 4 for length(ent) - 4), 8, '0'))::bit(32)::int;
        ELSE
          code := substring(ent from 3 for length(ent) - 3)::int;
        END IF;
        -- The branch order below is html.unescape()'s own, and it matters:
        -- 0x80..0x9F is in *both* the C1 table and the invalid-code-point set,
        -- and the table has to win. Two articles from one feed — one migrated,
        -- one re-upserted — must not end up displaying differently.
        IF code = 0 THEN
          txt := replace(txt, ent, chr(65533));
        ELSIF code = 13 THEN
          txt := replace(txt, ent, chr(13));
        ELSIF code BETWEEN 128 AND 159 THEN
          -- Postgres arrays are 1-based, so 0x80 lives at index 1.
          txt := replace(txt, ent, chr(c1_map[code - 127]));
        ELSIF code > 1114111 OR code BETWEEN 55296 AND 57343 THEN
          -- Past the Unicode range, or a lone surrogate: not encodable.
          txt := replace(txt, ent, chr(65533));
        ELSIF code BETWEEN 1 AND 8
           OR code = 11
           OR code BETWEEN 14 AND 31
           OR code = 127
           OR code BETWEEN 64976 AND 65007          -- 0xFDD0..0xFDEF
           OR (code & 65534) = 65534                -- 0xFFFE/0xFFFF in every plane
        THEN
          -- Control characters other than tab/LF/FF/CR, and the Unicode
          -- noncharacters. html.unescape() drops the reference entirely; chr()
          -- would happily store an invisible control character instead.
          txt := replace(txt, ent, '');
        ELSE
          txt := replace(txt, ent, chr(code));
        END IF;
      EXCEPTION WHEN others THEN
        -- Never fail the whole migration over one malformed reference.
        txt := replace(txt, ent, chr(65533));
      END;
    END LOOP;

    UPDATE articles SET summary = txt WHERE id = row_id AND summary IS DISTINCT FROM txt;
  END LOOP;
END $$;

-- 4. Decode named entities and tidy whitespace, on tag-shaped prose as well as
--    on what step 2 flattened. `&amp;` goes last, so a double-escaped `&amp;lt;`
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
                replace(summary, '&nbsp;', ' '),
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
      -- Postgres's [[:space:]] is narrower than Python's `\s`: it has U+3000 but
      -- not NBSP, and none of the separators below. The E-string turns each
      -- escape into the literal character before the regex sees it.
      E'[[:space:]\\u001C-\\u001F\\u0085\\u00A0\\u1680\\u2000-\\u200A\\u2028\\u2029\\u202F\\u205F]+',
      ' ', 'g'
    )
  ),
  ''
)
-- Every row, deliberately. An earlier version narrowed this to rows with an
-- entity, doubled whitespace, or leading/trailing whitespace — and silently
-- skipped `line one<TAB>line two`, which has none of those but still needs
-- normalizing. A one-time full pass costs a table rewrite the steps above are
-- already paying for, and removes a whole class of "does the predicate cover
-- this?" reasoning.
WHERE summary IS NOT NULL;

-- 5. A summary that was nothing but tags (`<p></p>`, a lone <img>) is now the
--    empty string, which step 4's WHERE does not match. Both are falsy to every
--    consumer, but the parser writes NULL, so store NULL here too.
UPDATE articles
SET summary = NULL
WHERE summary = '';
