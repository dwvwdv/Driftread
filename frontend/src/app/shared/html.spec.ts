import { looksLikeHtml, stripHtml } from './html';

describe('looksLikeHtml', () => {
  it('recognises a document by its closing or self-closing tag', () => {
    expect(looksLikeHtml('<p>hi</p>')).toBe(true);
    expect(looksLikeHtml('<div><img src="a.png"/></div>')).toBe(true);
    expect(looksLikeHtml('<img src="a.png"/>')).toBe(true);
  });

  it('leaves prose that merely quotes a tag alone', () => {
    // The reader picks its rendering branch on this, and a false positive runs
    // the destructive direction: [innerHTML] would swallow the `<p>` the
    // sentence is about, so the text disappears rather than just looking ugly.
    // An attribute is deliberately not enough — prose about HTML quotes
    // `<a href="…">` constantly.
    expect(looksLikeHtml('Use <p> for paragraphs')).toBe(false);
    expect(looksLikeHtml('quote <a href="https://x"> like so')).toBe(false);
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
