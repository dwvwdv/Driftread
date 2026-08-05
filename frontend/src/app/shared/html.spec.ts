import { looksLikeHtml, stripHtml } from './html';

describe('looksLikeHtml', () => {
  it('recognises markup', () => {
    expect(looksLikeHtml('<p>hi</p>')).toBe(true);
    expect(looksLikeHtml('a <br> b')).toBe(true);
    expect(looksLikeHtml('<!-- note -->')).toBe(true);
  });

  it('leaves comparisons alone', () => {
    // The reader picks its rendering branch on this. A false positive here
    // would send a plain summary through [innerHTML], and the sanitizer would
    // swallow "< 3 and y >" as if it were a tag.
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

  it('collapses whitespace and returns empty for nothing', () => {
    expect(stripHtml('  a \n\n b  ')).toBe('a b');
    expect(stripHtml(null)).toBe('');
    expect(stripHtml('<p></p>')).toBe('');
  });
});
