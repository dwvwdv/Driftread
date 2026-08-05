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

// The opening `<` must be followed by a letter or `/`, so prose like
// "if x < 3 and y > 2" survives intact.
const TAG_RE = /<!--[\s\S]*?-->|<\/?[a-zA-Z][^>]*>/g;
const DROP_WHOLE_RE = /<(script|style)\b[^>]*>[\s\S]*?<\/\1\s*>/gi;
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

/** Entities common enough in feed text to be worth decoding by hand. */
const ENTITIES: Record<string, string> = {
  '&amp;': '&',
  '&lt;': '<',
  '&gt;': '>',
  '&quot;': '"',
  '&#39;': "'",
  '&apos;': "'",
  '&nbsp;': ' ',
};

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
 * Mirrors _MARKUP_RE in backend/rss_parser.py.
 *
 * Non-global on purpose: `.test()` on a /g regex carries lastIndex between calls.
 */
const MARKUP_RE =
  /<\/[a-zA-Z]|<[a-zA-Z][^>]*\/>|<(?:area|base|br|col|embed|hr|img|input|link|meta|param|source|track|wbr)\b[^>]*=[^>]*>/i;

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
  return withoutTags
    .replace(/&(?:amp|lt|gt|quot|apos|nbsp|#39);/g, (e) => ENTITIES[e] ?? e)
    .replace(WS_RE, ' ')
    .trim();
}
