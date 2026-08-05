/**
 * Text-side handling of feed HTML.
 *
 * `Article.summary` is a *text* field — bookmark rows and the reader's
 * no-content fallback print it through interpolation. The parser now stores
 * plain text there (backend/rss_parser.py), but rows written before that fix
 * still hold whichever markup the publisher put in <description>, and an article
 * that has since scrolled out of its feed's window will never be re-upserted.
 * These helpers keep those rows readable instead of showing `<p>` on screen.
 *
 * Kept as string work rather than DOMParser so the same rules apply wherever
 * this runs, and so it stays cheap enough to call from a template binding.
 */

// Everything between a tag's name and its closing `>`, quote-aware.
//
// An attribute value may legally contain a raw `>` (`<p title="2 > 1">`), and a
// plain `[^>]*` stopped at that inner one, leaving `1">Hi` behind as "text". The
// three alternatives are mutually exclusive on their first character, so the
// star is deterministic and cannot backtrack exponentially on hostile input.
// Mirrors _ATTRS in backend/rss_parser.py.
const ATTRS = `(?:[^>"']|"[^"]*"|'[^']*')*`;

// The opening `<` must be followed by a letter or `/`, so prose like
// "if x < 3 and y > 2" survives intact. The plain `[^>]*` form is kept as a last
// alternative for a tag with an unbalanced quote (`<p title="unclosed>`), which
// the quote-aware form cannot match — it has to come second.
const TAG_RE = new RegExp(`<!--[\\s\\S]*?-->|</?[a-zA-Z]${ATTRS}>|</?[a-zA-Z][^>]*>`, 'g');
const DROP_WHOLE_RE = new RegExp(`<(script|style)\\b${ATTRS}>[\\s\\S]*?</\\1\\s*>`, 'gi');
const TAG_NAME_RE = /^<\/?\s*([a-zA-Z][a-zA-Z0-9]*)/;
const WS_RE = /\s+/g;

/**
 * Tags that imply a break in the text. Everything else is inline and is removed
 * outright — `<a>` and `<em>` sit inside a word, and turning them into spaces
 * sprayed gaps through Chinese text ("这里记录 开源 。").
 */
const BLOCK_TAGS = new Set([
  'address',
  'article',
  'aside',
  'blockquote',
  'br',
  'dd',
  'div',
  'dl',
  'dt',
  'figcaption',
  'figure',
  'footer',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'header',
  'hr',
  'li',
  'main',
  'nav',
  'ol',
  'p',
  'pre',
  'section',
  'table',
  'tbody',
  'td',
  'tfoot',
  'th',
  'thead',
  'tr',
  'ul',
]);

/** Named entities common enough in feed text to be worth decoding by hand. */
const NAMED_ENTITIES: Record<string, string> = {
  '&amp;': '&',
  '&lt;': '<',
  '&gt;': '>',
  '&quot;': '"',
  '&apos;': "'",
  '&nbsp;': ' ',
};

/**
 * Named entities plus numeric character references, in one alternation.
 *
 * One regex rather than a chain of replaces so the decode is a *single pass*,
 * matching Python's html.unescape(): scanning resumes after each match, so a
 * double-escaped `&amp;#8217;` yields the literal text `&#8217;` instead of
 * being decoded twice.
 *
 * The numeric forms matter — feeds are full of `&#8217;` for a curly apostrophe
 * and `&#8212;` / `&#x2014;` for an em dash. Mirrors rss_parser.py and step 3 of
 * migration 009.
 */
const ENTITY_RE = /&(?:amp|lt|gt|quot|apos|nbsp|#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6});/g;

/**
 * Code points for 0x80..0x9F, in order.
 *
 * html.unescape() does not treat a numeric reference in the C1 range as a
 * control character: per the HTML5 spec it substitutes the Windows-1252
 * character, so `&#151;` is an em dash and `&#128;` is a euro sign. Older feeds
 * — anything downstream of Word — emit these constantly. Generated from Python's
 * html._invalid_charrefs rather than transcribed; five slots (0x81, 0x8D, 0x8F,
 * 0x90, 0x9D) map to themselves, as in Python. Mirrors c1_map in migration 009.
 */
const C1_REPLACEMENTS = [
  8364, 129, 8218, 402, 8222, 8230, 8224, 8225, 710, 8240, 352, 8249, 338, 141, 381, 143, 144, 8216,
  8217, 8220, 8221, 8226, 8211, 8212, 732, 8482, 353, 8250, 339, 157, 382, 376,
];

/**
 * Code points html.unescape() drops the reference for outright: control
 * characters other than tab/LF/FF/CR, and the Unicode noncharacters. The last
 * test covers 0xFFFE/0xFFFF in every plane, which is 17 ranges in one line.
 */
function isDroppedCodePoint(code: number): boolean {
  return (
    (code >= 0x01 && code <= 0x08) ||
    code === 0x0b ||
    (code >= 0x0e && code <= 0x1f) ||
    code === 0x7f ||
    (code >= 0xfdd0 && code <= 0xfdef) ||
    (code & 0xfffe) === 0xfffe
  );
}

function decodeEntity(entity: string): string {
  const named = NAMED_ENTITIES[entity];
  if (named !== undefined) return named;

  const body = entity.slice(2, -1);
  const code =
    body[0] === 'x' || body[0] === 'X' ? parseInt(body.slice(1), 16) : parseInt(body, 10);

  // Branch order is html.unescape()'s own and it matters: 0x80..0x9F is in both
  // the C1 table and the dropped set, and the table has to win.
  if (code === 0) return '�';
  if (code === 0x0d) return '\r';
  if (code >= 0x80 && code <= 0x9f) return String.fromCodePoint(C1_REPLACEMENTS[code - 0x80]);
  if (code > 0x10ffff || (code >= 0xd800 && code <= 0xdfff)) return '�';
  if (isDroppedCodePoint(code)) return '';
  return String.fromCodePoint(code);
}

const VOID_ELEMENTS = 'area|base|br|col|embed|hr|img|input|link|meta|param|source|track|wbr';

/**
 * Tells an HTML *document* apart from prose that happens to mention a tag.
 *
 * A plain summary can legitimately contain tag-shaped text — the parser stores
 * "Use <p> for paragraphs" exactly like that, because that is what the publisher
 * wrote. Deciding on "contains a tag" classified that sentence as legacy markup,
 * and the failure ran the bad direction: the reader fed it to [innerHTML] and the
 * bookmark row stripped it, so the `<p>` vanished from the page instead of merely
 * looking ugly.
 *
 * Three shapes count, and the asymmetry above is why the list stops where it does:
 *
 *   `</x>`         a closing tag. Markup that encloses anything has one; prose
 *                  quoting a tag name essentially never does.
 *   `<x …/>`       explicitly self-closed.
 *   `<img src=…>`  a void element *carrying an attribute*. This is what makes an
 *                  image-only summary work — common on photo blogs and webcomics,
 *                  where the image is the article. Restricted to void elements on
 *                  purpose: "an attribute" on its own would swallow prose quoting
 *                  `<a href="…">`, which tech writing does daily.
 *
 * A *bare* void tag stays out. "one<br>two" really is markup, but so is "use
 * <br> to break lines", and only the second one loses text if we guess wrong.
 * Misjudging the first only shows a `<br>` on screen: visible, and fixable.
 *
 * The void-element branch requires the tag to actually close with no `<`
 * intervening; without both, unclosed prose like "the <img tag is useful, x = 1"
 * reaches an unrelated `=` further down the sentence and gets called markup.
 *
 * Mirrors _MARKUP_RE in backend/rss_parser.py.
 *
 * Non-global on purpose: `.test()` on a /g regex carries lastIndex between calls.
 */
const MARKUP_RE = new RegExp(
  `</[a-zA-Z]|<[a-zA-Z]${ATTRS}/>|<(?:${VOID_ELEMENTS})\\b[^><]*=[^><]*>`,
  'i',
);

export function looksLikeHtml(value: string | null | undefined): boolean {
  return !!value && MARKUP_RE.test(value);
}

/**
 * Flatten HTML source to readable text. Returns '' for null/undefined.
 *
 * Tags are only stripped from something that is actually a document, for the
 * reason above: stripping "Use <p> for paragraphs" would delete the one token
 * the sentence is about.
 */
export function stripHtml(value: string | null | undefined): string {
  if (!value) return '';
  const withoutTags = looksLikeHtml(value)
    ? value.replace(DROP_WHOLE_RE, ' ').replace(TAG_RE, (tag) => {
        const name = TAG_NAME_RE.exec(tag);
        return name && BLOCK_TAGS.has(name[1].toLowerCase()) ? ' ' : '';
      })
    : value;
  // Decoded last either way, so a `&lt;p&gt;` that survived double-escaping
  // upstream becomes visible text rather than a parsed tag.
  return withoutTags.replace(ENTITY_RE, decodeEntity).replace(WS_RE, ' ').trim();
}
