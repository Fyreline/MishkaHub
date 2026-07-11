#!/usr/bin/env node
// Rasterises public/film-cat-icon.svg into apple-touch-icon.png. Run manually,
// only when the mark changes:
//   node scripts/generate-apple-touch-icon.mjs
// Dev-only (sharp is a devDependency, Japan_website precedent) — never
// imported by app code, never shipped.
//
// iOS 26's home-screen treatment (the layered "liquid glass" look) needs a
// full-bleed OPAQUE square: no transparency, no baked rounded corners — the
// OS applies its own mask. The old icon was the transparent favicon tile,
// which iOS composited onto a flat slab. See HOUSEHOLD-DESIGN.md §8.
//
// Hex exception: icon-generation scripts can't read CSS custom properties —
// values must match theme.css's light `paper` exactly (the glyph's clay is
// baked into film-cat-icon.svg, the documented favicon exception).
const PAPER = '#f7fbfa'

import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import sharp from 'sharp'

const __dirname = dirname(fileURLToPath(import.meta.url))
const publicDir = join(__dirname, '..', 'public')

// The favicon tile is 32x32 with the camera-cat art spanning ~74% of it, so a
// 160px tile puts the glyph at ~118px ≈ 66% of the 180 canvas — the same
// framing as Michi's and Japan's apple-touch-icons.
const glyph = await sharp(join(publicDir, 'film-cat-icon.svg'), { density: 384 })
  .resize(160, 160)
  .png()
  .toBuffer()

await sharp({
  create: { width: 180, height: 180, channels: 4, background: PAPER },
})
  .composite([{ input: glyph }]) // default gravity: centre
  .flatten({ background: PAPER })
  .removeAlpha()
  .png()
  .toFile(join(publicDir, 'apple-touch-icon.png'))

console.log('wrote apple-touch-icon.png (180x180, opaque, full-bleed)')
