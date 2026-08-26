# Troubleshooting

Run `argus-test-creator doctor [project]` first.

| Symptom | Cause / fix |
| --- | --- |
| "Argus is not installed or not found" | Install Argus; put `argus` on PATH or set `ARGUS_EXECUTABLE`; or `argus: {executable: …}` in config |
| Browser target: "needs a URL" | Target → Settings… → URL |
| Browser fails to open | `playwright install chromium`; check the URL is reachable |
| Desktop: screenshot/permission errors (macOS) | System Settings → Privacy & Security → Screen Recording and Input Monitoring for your terminal/app |
| Android: "No authorized Android device" | enable USB debugging, accept the RSA prompt, `adb devices` shows `device` |
| OCR unavailable | install tesseract (`brew install tesseract` / `apt install tesseract-ocr`) and `pip install 'argus-test-creator[ocr]'`; the fake target uses deterministic fake OCR |
| Suggestions never appear | `recording.suggest_assertions` is false, OCR unavailable, or the screen did not change (>1 % pixels) |
| "Recording failed — Screenshot" | device disconnected; reconnect and retry; the session journal keeps everything captured so far |
| Recovering after a crash | Target → Recover interrupted recording… |
| Argus rejects the test | Validation tab shows Argus's message; common causes: missing `feature`, duplicate id in another file, missing asset |
| `report.json` not found after a run | Argus exited before writing results (exit 2/3) — read the Run tab output |
| GUI does not start | `pip install 'argus-test-creator[ui]'`; on Linux install Qt xcb libs |

Enable diagnostic mode (`diagnostic: true` or `ARGUS_CREATOR_DIAGNOSTIC=true`) to see technical
details for every failure and DEBUG logs. Secrets (tokens, passwords) are redacted from logs.
