import { looksLikeHtml, stripHtml } from './html';

describe('looksLikeHtml', () => {
  it('recognises a closing tag, a self-closing tag, or a void tag with an attribute', () => {
    expect(looksLikeHtml('<p>hi</p>')).toBe(true);
    expect(looksLikeHtml('<div><img src="a.png"/></div>')).toBe(true);
    expect(looksLikeHtml('<img src="a.png"/>')).toBe(true);
    // No slash, no closing tag — but an image-only summary is the whole item on
    // a photo blog, so it has to follow the markup path.
    expect(looksLikeHtml('<img src="a.png">')).toBe(true);
    expect(looksLikeHtml('<IMG SRC="a.png">')).toBe(true);
  });

  it('leaves prose that merely quotes a tag alone', () => {
    // The reader picks its rendering branch on this, and a false positive runs
    // the destructive direction: [innerHTML] would swallow the `<p>` the
    // sentence is about, so the text disappears rather than just looking ugly.
    expect(looksLikeHtml('Use <p> for paragraphs')).toBe(false);
    // Void-element attributes count; attributes in general do not. Tech writing
    // quotes `<a href="…">` constantly.
    expect(looksLikeHtml('quote <a href="https://x"> like so')).toBe(false);
    // The void branch needs the tag to close with no `<` in between, or an
    // unrelated `=` further down an unclosed sentence counts as an attribute.
    expect(looksLikeHtml('the <img tag is useful, x = 1')).toBe(false);
    // …while a genuine one still counts even with a `>` inside the attribute.
    expect(looksLikeHtml('<img alt="a > b">')).toBe(true);
  });

  it('leaves a bare void tag as text', () => {
    // "one<br>two" is markup and "use <br> to break lines" is prose, and the
    // two are indistinguishable. Only the second loses text if we guess wrong,
    // so the tie goes to leaving it alone.
    expect(looksLikeHtml('one<br>two')).toBe(false);
    expect(looksLikeHtml('use <br> to break lines')).toBe(false);
    expect(stripHtml('use <br> to break lines')).toBe('use <br> to break lines');
  });

  it('leaves comparisons alone', () => {
    expect(looksLikeHtml('if x < 3 and y > 2 then done')).toBe(false);
    expect(looksLikeHtml('5 > 3')).toBe(false);
  });

  it('is false for empty input', () => {
    expect(looksLikeHtml(null)).toBe(false);
    expect(looksLikeHtml(undefined)).toBe(false);
    expect(looksLikeHtml('')).toBe(false);
  });

  it('does not carry regex state between calls', () => {
    expect(looksLikeHtml('<p>one</p>')).toBe(true);
    expect(looksLikeHtml('<p>two</p>')).toBe(true);
  });
});

describe('stripHtml', () => {
  it('breaks on block tags and closes up inline ones', () => {
    // No space around the inline <a>: in Chinese that reads as a typo.
    expect(stripHtml('<p>第一段<a href="x">連結</a>。</p><p>第二段</p>')).toBe(
      '第一段連結。 第二段',
    );
    expect(stripHtml('one<br>two<span>three</span>')).toBe('one twothree');
  });

  it('walks quoted attribute values containing a raw >', () => {
    // `<p title="2 > 1">` is valid HTML. A `[^>]*` pattern stops at the inner
    // `>` and leaves `1">Hi` behind as "text".
    expect(stripHtml('<p title="2 > 1">Hi</p>')).toBe('Hi');
    expect(stripHtml('<a href="x" title="a > b">link</a> tail')).toBe('link tail');
    expect(stripHtml("<p title='2 > 1'>Hi</p>")).toBe('Hi');
    // An unbalanced quote can't be walked, so the plain fallback branch matters.
    expect(stripHtml('<p title="unclosed>Hi</p>')).toBe('Hi');
  });

  it('does not backtrack on hostile input', () => {
    const started = Date.now();
    stripHtml('<p ' + 'a="'.repeat(20000));
    stripHtml('<p ' + 'x'.repeat(20000));
    expect(Date.now() - started).toBeLessThan(1000);
  });

  it('drops script and style bodies whole', () => {
    expect(stripHtml('<style>p{color:red}</style><p>Hi</p><script>alert(1)</script>')).toBe('Hi');
  });

  it('decodes entities after stripping tags', () => {
    expect(stripHtml('<p>Tom &amp; Jerry wrote &amp;lt;p&amp;gt;</p>')).toBe(
      'Tom & Jerry wrote &lt;p&gt;',
    );
  });

  it('leaves plain text intact', () => {
    expect(stripHtml('if x < 3 and y > 2 then done')).toBe('if x < 3 and y > 2 then done');
  });

  it('does not strip tag-shaped prose', () => {
    // Stripping here would delete the one token the sentence is about.
    expect(stripHtml('Use <p> for paragraphs')).toBe('Use <p> for paragraphs');
    expect(stripHtml('quote <a href="https://x"> like so')).toBe(
      'quote <a href="https://x"> like so',
    );
  });

  it('collapses whitespace and returns empty for nothing', () => {
    expect(stripHtml('  a \n\n b  ')).toBe('a b');
    expect(stripHtml(null)).toBe('');
    expect(stripHtml('<p></p>')).toBe('');
  });
});
