import {
  Shippori_Mincho_B1,
  Spectral,
  Zen_Kaku_Gothic_New,
} from "next/font/google";

// next/font/google self-hosts these from Google Fonts at build time. For the two
// Japanese faces we set `preload: false` on purpose: Google slices the CJK glyph
// set into many `unicode-range` `@font-face` blocks, and the whole sliced set is
// downloaded and served from our own origin — kanji/かな render correctly, loaded
// on demand per slice. `subsets: ["latin"]` only narrows what gets `<link rel=
// preload>`; it never drops glyphs. Preloading a single CJK face would pull a huge
// slice up front, so we let `display: swap` handle it instead.
//
// Each call exposes a CSS variable (`--font-*`) consumed in globals.css via
// `var(--font-*, <system fallback>)`, so isolated renders (Storybook, unit tests)
// that don't mount <html> still fall back cleanly.

export const shipporiMincho = Shippori_Mincho_B1({
  weight: ["500", "700", "800"],
  subsets: ["latin"],
  display: "swap",
  preload: false,
  variable: "--font-shippori-mincho",
  fallback: ["Hiragino Mincho ProN", "serif"],
});

export const zenKaku = Zen_Kaku_Gothic_New({
  weight: ["400", "500", "700"],
  subsets: ["latin"],
  display: "swap",
  preload: false,
  variable: "--font-zen-kaku",
  fallback: ["system-ui", "Hiragino Sans", "Noto Sans JP", "sans-serif"],
});

// Same face as `zenKaku` (the woff2 dedupe to identical hashed files), but a
// *monospace* fallback chain so numeric cells (`--font-num`) keep tabular-nums
// alignment even if Zen Kaku fails to load. Without this the var() would resolve
// to `zenKaku`'s sans-serif fallback, silently losing the monospace tail the
// original CDN stack guaranteed.
export const zenKakuMono = Zen_Kaku_Gothic_New({
  weight: ["400", "500", "700"],
  subsets: ["latin"],
  display: "swap",
  preload: false,
  variable: "--font-zen-kaku-mono",
  fallback: ["ui-monospace", "SF Mono", "Menlo", "monospace"],
});

// Spectral is a Latin-only face (italic subtitles); preloading its latin slice is
// cheap and worthwhile, so we keep the default `preload: true`.
export const spectral = Spectral({
  weight: ["500"],
  style: ["normal", "italic"],
  subsets: ["latin"],
  display: "swap",
  variable: "--font-spectral",
  fallback: ["serif"],
});
