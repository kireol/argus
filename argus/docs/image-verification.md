# Image Verification

Visual verification compares what is *actually on screen* against reference
images using OpenCV. It is the framework's source of truth — instrumentation
can help diagnose, but only pixels pass tests.

## How matching works

`image_present` / `image_not_present` use **template matching**
(`TM_CCOEFF_NORMED` by default): the reference image slides across the
screenshot and the best correlation becomes the confidence score (0..1).

```yaml
- action: verify
  condition:
    type: image_present
    image: movie_123.png     # resolved against asset_paths
    threshold: 0.90          # pass at or above this confidence
    region: movie_artwork    # optional: search only here
    grayscale: false         # optional: match ignoring color
    scale_tolerance: 0.1     # optional: also try 90% / 110% template size
                             # (a reference larger than the region is
                             # auto-shrunk to fit; this is extra slack)
    mask_background: true    # optional: ignore near-black pixels in the reference
```

### Background-independent icon matching

Screen-crop references usually include the background wallpaper behind the icon.
Plain template matching then fails when that wallpaper changes (even if the
icon itself is identical). Set `mask_background: true` to match only the
bright/icon pixels in the reference (luminance above `mask_luminance`, default
30). Argus switches to a mask-compatible OpenCV method automatically.

Use this for telltales / turn signals captured as crops on black (or any dark)
chrome. Leave it off for full-bleed screenshot comparisons where the background
is part of the assertion.

A passing result reports where the image was found:

```json
{
  "passed": true,
  "confidence": 0.96,
  "location": {"x": 422, "y": 183, "width": 200, "height": 200}
}
```

`screenshot_matches` compares a whole screenshot (or region) with a
reference using mean absolute difference — never pixel-perfect equality;
`threshold: 1.0` would demand identical images, `0.98` tolerates encoding
and antialiasing noise.

## Choosing thresholds

- `0.90` (the default) is a good start for distinctive artwork.
- Raise toward `0.95–0.98` when near-identical variants must be told apart.
- Lower toward `0.80` only when rendering legitimately varies (gradients,
  video frames) — and prefer a tighter `region` instead.
- Set the global default in configuration, override per condition:

```yaml
verification:
  image:
    default_threshold: 0.90
```

## Regions (spec §19, §51)

Cropping before matching is the single biggest performance and reliability
win — don't scan a 4K frame for a 200px badge. Define named regions once:

```yaml
regions:
  movie_artwork: {x: 100, y: 100, width: 500, height: 400}
```

and use `region: movie_artwork` in any visual condition, or inline a region:

```yaml
region: {x: 300, y: 100, width: 500, height: 400}
```

Reported locations are always absolute screen coordinates, even when a
region was searched.

## Performance model (spec §19, §34)

- **Capture once, verify many:** within one condition-evaluation pass
  (including `all`/`any` composites) a single screenshot feeds every visual
  sub-condition.
- **Reference images are cached** in memory after first load — a
  `wait_until` poll loop never re-reads PNGs from disk.
- **Regions crop before matching**, so cost scales with the region, not the
  screen.
- **Auto-fit oversized references:** if the PNG is larger than the search
  area (for example a 96×112 golden in an 80×80 region), Argus downscales
  it to the largest size that still fits. Tests do not need `scale_tolerance`
  for that case. Matching still fails if even a 16px template cannot fit.
- **Multiscale early exit:** with `scale_tolerance`, Argus tries scale `1.0`
  first (then nearest scales) and stops once confidence meets the threshold.
- **`wait_until` polls skip screen-info probes** (`wm size` / density); only
  the screenshot is captured each cycle.
- **`verify` after a matching `wait_until`** reuses the wait result by default
  (`wait.reuse_wait_result_on_verify`) — no second screencap/match when the
  conditions are identical and nothing ran in between.
- Default `wait.default_poll_interval` is `500ms` (override per step or in
  config). Grayscale matching is ~3× cheaper than color when color isn't the
  signal.

## Making good reference images

1. Capture from the real device at the real resolution
   (`screenshot` action, or a failure's `actual.png`).
2. Crop tightly to the distinctive part — logos, artwork, icons. Avoid
   including background that may change.
3. Avoid regions with animation, clocks, or antialiased text at small sizes.
4. Store under `assets/images/` with names tests can reference
   (`movie_123.png`).
5. Screen resolutions differ per device — don't assume one; Argus will
   shrink a reference that does not fit the region. Use `scale_tolerance`
   when the *on-screen* icon is a different size than the (fitted)
   reference, or keep per-platform images under `assets/images/<WxH>/`.

## Text (OCR)

`text_present` / `text_not_present` run OCR (Tesseract by default) on the
screenshot or a region of it. OCR is optional: install with
`pip install "argus[ocr]"` plus the `tesseract` binary. Tests that don't use
text conditions never require it — preflight only checks OCR when a selected
test needs it.

OCR tips: prefer regions around the text, large/high-contrast text reads
best, and use `case_sensitive: false` (the default) unless case matters.

## Diagnosing failures

Every visual failure saves to the test's artifact directory:

| File | Contents |
| --- | --- |
| `actual.png` | the screenshot that failed verification |
| `expected.png` | the reference image |
| `diff.png` | absolute difference visualization |
| `metadata.json` | confidence, threshold, region, timings |

Look at `actual.png` first: nine times out of ten the screen is simply not
showing what you expected, and the confidence score tells you how close it
was.

To keep comparison images for **passing** image verifies as well (so the HTML
report always shows what was compared):

```yaml
results:
  save_comparison_images: true
```

Or per run:

```bash
argus run --save-comparisons --feature DoorAjar
```
