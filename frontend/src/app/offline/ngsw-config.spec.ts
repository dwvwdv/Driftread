import config from '../../../ngsw-config.json';
import manifest from '../../../public/manifest.webmanifest' with { type: 'json' };

/**
 * The service worker caches the app shell only. Private data must never land
 * in Cache Storage: /api/ responses (and anything carrying Authorization) are
 * per-account, and a shared cache would leak them across sign-ins. env.js is
 * rendered at container start and must stay network-first.
 */

/** ngsw glob → RegExp, same rules as @angular/service-worker's urlToRegex. */
function globToRegex(glob: string): RegExp {
  const source = glob
    .split(/(\*\*\/|\*\*|\*|\?)/)
    .map((part) => {
      if (part === '**/') return '(?:.+/)?';
      if (part === '**') return '.*';
      if (part === '*') return '[^/]*';
      if (part === '?') return '[^/]';
      return part.replace(/[.+^${}()|[\]\\]/g, '\\$&');
    })
    .join('');
  return new RegExp(`^${source}$`);
}

function matches(patterns: string[], url: string): boolean {
  let hit = false;
  for (const pattern of patterns) {
    const negative = pattern.startsWith('!');
    if (globToRegex(negative ? pattern.slice(1) : pattern).test(url)) hit = !negative;
  }
  return hit;
}

const PRIVATE_URLS = [
  '/api/me/sync',
  '/api/me/sync/operations',
  '/api/me/stream',
  '/api/me/rss',
  '/api/mcp/',
  '/api/articles/1',
  '/.well-known/oauth-authorization-server',
];

describe('ngsw-config.json', () => {
  const assetFiles = config.assetGroups.flatMap((g) => g.resources.files ?? []);
  const dataUrls = config.dataGroups.flatMap((g) => g.urls);

  it('never caches API or OAuth discovery responses', () => {
    for (const url of PRIVATE_URLS) {
      expect(matches(assetFiles, url), url).toBe(false);
      expect(matches(dataUrls, url), url).toBe(false);
    }
    expect(config.assetGroups.every((g) => !('urls' in g.resources))).toBe(true);
  });

  it('caches the app shell (hashed bundles, index.html, manifest, icons)', () => {
    for (const url of [
      '/index.html',
      '/main-ABC123.js',
      '/chunk-XYZ.js',
      '/styles-1.css',
      '/manifest.webmanifest',
      '/icons/icon-192.png',
    ]) {
      expect(matches(assetFiles, url), url).toBe(true);
    }
  });

  it('keeps the runtime config out of the app shell and network-first', () => {
    expect(matches(assetFiles, '/env.js')).toBe(false);
    expect(config.dataGroups).toEqual([
      expect.objectContaining({
        urls: ['/env.js'],
        cacheConfig: expect.objectContaining({ strategy: 'freshness' }),
      }),
    ]);
  });

  it('does not answer API or .well-known navigations with index.html', () => {
    expect(matches(config.navigationUrls, '/me/offline')).toBe(true);
    expect(matches(config.navigationUrls, '/api/me/rss')).toBe(false);
    expect(matches(config.navigationUrls, '/.well-known/openid-configuration')).toBe(false);
  });
});

describe('manifest.webmanifest', () => {
  it('is installable standalone with Offbeat colours and maskable icons', () => {
    expect(manifest.display).toBe('standalone');
    expect(manifest.start_url).toBe('/');
    // Dark Offbeat surface-base (Nord Polar Night 0), matching index.html theme-color.
    expect(manifest.theme_color).toBe('#2e3440');
    expect(manifest.background_color).toBe('#2e3440');
    const sizes = manifest.icons.map((i) => `${i.sizes}:${i.purpose}`);
    expect(sizes).toEqual(
      expect.arrayContaining(['192x192:any', '512x512:any', '512x512:maskable']),
    );
  });
});
