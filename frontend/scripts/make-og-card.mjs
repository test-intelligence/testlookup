#!/usr/bin/env node
/* global console, process */
/**
 * Render public/og-card.png, the social-card image a shared TestLookup link
 * unfurls with (W25b HYG-09): the repo-root testLookup_Logo.png (1024 x 558,
 * a black pill with the white wordmark) scaled to 880 px wide (~480 px high)
 * and centred on the brand background #0d1117 (public/app-icon.svg) at
 * 1200 x 630, the Open Graph large-image size. index.html points og:image at
 * it; nginx fills in the absolute origin (sub_filter, see nginx.conf.template).
 *
 * Run by hand, once per redesign, from frontend/:
 *
 *   OG_CHANNEL=chrome node scripts/make-og-card.mjs
 *
 * (OG_CHANNEL picks an installed browser channel; unset = Playwright's own
 * Chromium.) No image library: Playwright is already a devDependency, so the
 * browser does the scaling. The PNG is committed and served as-is.
 *
 * Never run by CI or by the image build: it reads ../testLookup_Logo.png, and
 * the frontend image's build context is frontend/ only. Nothing under src/
 * imports it, and `npm run lint` covers src/ only.
 *
 * The static-asset location caches *.png for a year as immutable, so a new
 * design ships under a NEW file name (og-card-2.png) plus a one-line change to
 * index.html's og:image, never by overwriting this file in place.
 */
import { chromium } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), '..')
const LOGO = join(FRONTEND, '..', 'testLookup_Logo.png')
const OUT = join(FRONTEND, 'public', 'og-card.png')

const WIDTH = 1200
const HEIGHT = 630
const LOGO_WIDTH = 880
const BACKGROUND = '#0d1117'

const logo = readFileSync(LOGO).toString('base64')
const html = `<!doctype html>
<html><head><meta charset="utf-8"><style>
  html, body { margin: 0; width: ${WIDTH}px; height: ${HEIGHT}px; overflow: hidden; background: ${BACKGROUND}; }
  body { display: flex; align-items: center; justify-content: center; }
  img { display: block; width: ${LOGO_WIDTH}px; height: auto; }
</style></head>
<body><img alt="" src="data:image/png;base64,${logo}"></body></html>`

const browser = await chromium.launch({ channel: process.env.OG_CHANNEL || undefined })
try {
  const page = await browser.newPage({ viewport: { width: WIDTH, height: HEIGHT }, deviceScaleFactor: 1 })
  await page.setContent(html, { waitUntil: 'load' })
  // The data: URL is decoded asynchronously; screenshot only a painted logo.
  await page.locator('img').evaluate((img) => img.decode())
  const box = await page.locator('img').boundingBox()
  await page.screenshot({ path: OUT, clip: { x: 0, y: 0, width: WIDTH, height: HEIGHT } })
  console.log(`logo drawn at x=${box.x} y=${box.y} ${box.width} x ${box.height}`)
} finally {
  await browser.close()
}

// PNG: 8-byte signature, then the IHDR chunk (length, "IHDR", width, height).
const png = readFileSync(OUT)
const width = png.readUInt32BE(16)
const height = png.readUInt32BE(20)
console.log(`public/og-card.png: IHDR ${width} x ${height}, ${png.length} bytes`)
if (png.toString('latin1', 12, 16) !== 'IHDR' || width !== WIDTH || height !== HEIGHT) {
  console.error(`expected a ${WIDTH} x ${HEIGHT} PNG`)
  process.exitCode = 1
}
