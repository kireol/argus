# Assertion guide

The Creator authors Argus conditions; it never invents its own assertion language.

## Types offered (filtered by target capabilities)

| Menu label | Argus condition | Needs |
| --- | --- | --- |
| Text is visible / NOT visible | `text_present` / `text_not_present` | OCR |
| Image is visible / NOT visible | `image_present` / `image_not_present` | screenshot |
| Screen/region matches reference | `screenshot_matches` | screenshot |
| Pixel matches color | `pixel_matches` | screenshot |
| Application status/state value | `instrumentation_value` / `application_state` | instrumentation |
| Backend value | `backend_value` | backend |
| Log contains | `log_contains` | logs |
| Media is playing | `now_playing` | playback state |

The first five are available in **Add Verification**; the rest through **Add Step → verify /
wait_until** with the condition editor. Composite conditions (`all` / `any` / `not`) can be edited
as YAML in the same editor and are preserved on import.

## Visual assertions

```text
current screenshot → drag rectangle → crop preview → choose type → threshold → asset name → insert
```

The crop is written to the workspace, then *promoted* into `assets/images/<name>_<hash8>.png`.
Identical pixels never produce a second file. The generated step:

```yaml
- action: wait_until
  condition:
    type: image_present
    image: batman_row_c7bc61f4.png
    threshold: 0.9
  timeout: 10s
```

Tick *Restrict search to the selected region* to add `region: {x, y, width, height}`.
`screenshot_matches` always carries the region (a whole-screen comparison is flagged by the
quality analyzer).

## OCR assertions

OCR runs in the background; the dialog lists detected lines with bounding boxes drawn on the
screenshot. Selecting a line fills the text; options: case sensitive, region, negation
(`text_not_present`, which is always a one-shot `verify`).

## Suggestions

After each recorded action the Creator compares the before/after captures (numpy diff), runs
OCR on the new frame and proposes, deterministically:

* `text_present` for newly visible text (longest strings first, at most 3);
* an `image_present` region for the changed area when it is neither tiny nor most of the screen.

Suggestions appear in the **Suggested verifications** panel and become steps only when you press
**Add** (provenance: `suggestion`).

## Synchronization

Prefer *wait for it* (`wait_until` + `timeout`) after an action and a one-shot `verify` only for
states that are already stable. Argus reuses a successful `wait_until` result for an identical
`verify` that follows it. The quality analyzer warns about fixed `wait` steps, missing
synchronization, missing verification, redundant taps, whole-screen comparisons, low thresholds,
unbounded OCR, missing assets, unresolved variables and platform mismatches.
