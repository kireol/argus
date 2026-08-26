# Packaging

Packaging configuration lives in `packaging/` and `scripts/`, separate from application code.

## Wheel / sdist

```bash
uv build            # dist/argus_test_creator-*.whl
pipx install 'argus-test-creator[ui,ocr,browser]'
```

## Standalone bundles (PyInstaller)

```bash
uv pip install --python ../.venv/bin/python pyinstaller
scripts/build-package.sh          # → dist/ArgusTestCreator(.app|.exe|/)
```

`packaging/pyinstaller.spec` bundles the GUI entry point, Qt plugins, the demo assets and the
entry-point metadata. External tools are *not* bundled: users install Argus, Playwright browsers
(`playwright install chromium`), Tesseract and ADB separately; `doctor` reports what is missing.

| Platform | Output | Notes |
| --- | --- | --- |
| macOS | `ArgusTestCreator.app` | sign/notarize before distribution; Screen Recording / Input Monitoring permissions are requested at first use |
| Windows | `ArgusTestCreator.exe` (one-dir) | |
| Linux | `ArgusTestCreator/` one-dir | Qt xcb dependencies must exist on the host |

Developer mode (`argus-test-creator gui` from a virtualenv) is always supported.
