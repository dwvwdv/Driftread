/** Lets specs import public/manifest.webmanifest (JSON) with { type: 'json' }. */
declare module '*.webmanifest' {
  const manifest: {
    display: string;
    start_url: string;
    theme_color: string;
    background_color: string;
    icons: { src: string; sizes: string; type: string; purpose: string }[];
  };
  export default manifest;
}
