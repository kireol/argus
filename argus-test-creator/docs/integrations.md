# Argus integration

`integrations/argus/integration.py` is the only module that talks to Argus.

## Discovery

Order: configured path (`argus.executable`) → `ARGUS_EXECUTABLE` → `<project>/.venv/bin/argus`
→ `PATH` → `<sys.prefix>/bin/argus`. Each candidate is probed with `--version`. In the
monorepo install both commands share one `.venv/`, so the `sys.prefix` step always finds Argus. If nothing is
found the Creator explains exactly how to configure it (`INSTALL_HINT`).

## Validation

`argus validate --config argus.yaml --framework-only` loads and validates every test under the
configured `test_paths` (definition errors → "✗ Test definitions … " / exit 3). `argus list`
confirms the test id is discoverable. Both outputs are parsed into `ValidationIssue`s with
`source="argus"`.

## Running

`argus run --config argus.yaml --test <ID> --no-logs` in the project directory. Output is
streamed line by line to the Run panel. Exit codes: 0 passed, 1 failed, 2 definition error,
3 preflight/setup failed. The newest directory under `results.dir` is the run dir;
`report.json` (schema_version 1) and `report.html` are read from it. Runs can be cancelled
(`terminate`) and are killed after `argus.run_timeout`.

## Schema inspection

`ArgusIntegration.inspect_schema()` runs a one-line script with Argus's own interpreter to list
registered action and condition names. `tests/integration/test_argus_schema_sync.py` fails when
`argus_schema/` drifts from the installed Argus.

## Generated `argus.yaml`

```yaml
test_paths: [tests]
asset_paths: [assets/images]
results: {dir: results, retain_on_success: true}
devices:
  web:                       # from TargetProfile.argus_device_name
    type: browser
    platform: web
    url: http://127.0.0.1:3210/
    browser: chromium
    viewport: [1280, 720]
    headless: true
```

Existing devices are preserved; only the selected target's entry is upserted.

## Fake target replay

Argus's fake device serves PNGs from `screenshot_dir` in order and holds the last one. When a
test recorded on the fake target is saved, the Creator copies the recording's final screen to
`assets/frames/frame_001.png` and points the `demo` device at it, so the demo test genuinely runs
(and its assertions genuinely evaluate) in Argus without hardware.

## Known limitations

* Argus validates whole suites, not single files; a broken sibling test makes `validate`
  fail — the message names the file.
* `report.json` is located by directory mtime; concurrent Argus runs in the same project could
  confuse it.
