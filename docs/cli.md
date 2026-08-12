# CLI Reference

```text
utf [GLOBAL OPTIONS] COMMAND [OPTIONS]
```

Global options (before the command): `--config/-c FILE`, `--log-level LEVEL`,
`--verbose/-v`, `--quiet/-q`, `--dry-run`, `--version`.
`--config` is also accepted directly on `run`, `list`, and `validate`.

## utf run

Run tests, filtered any way you like.

```bash
utf run                                   # everything
utf run --all                             # same, explicit
utf run --test MOV-001 --test MOV-002     # by ID
utf run --feature movies                  # by feature (case-insensitive)
utf run --tag smoke                       # by tag (repeatable, ANDed)
utf run --tag "smoke and not slow"        # boolean tag expression
utf run --platform android                # by platform
utf run --platform android --feature movies --tag smoke   # combined
```

Failure policy (spec §24 — centralized, default stop-on-failure):

```bash
utf run --stop-on-failure        # default: stop at first failure
utf run --continue-on-failure    # run everything, report at the end
utf run --max-failures 5         # stop after N failures
```

Other flags: `--skip-preflight` (not recommended), `--dry-run`,
`--config FILE`.

Exit codes: `0` all passed · `1` failures (or no tests matched) ·
`2` configuration/definition error · `3` preflight failed.

Reports land in `results/<timestamp>/`: `report.json`, `junit.xml`,
`report.html`, plus per-test artifact directories for failures.

## utf validate

Environment diagnosis, section by section, with
✓ (ok) / ⚠ (optional problem) / ✗ (blocking) / ○ (not configured):

```bash
utf validate                    # framework + backend + every device
utf validate --framework-only   # installation check, no hardware needed
```

Exit code `0` when READY, `3` otherwise. Optional components (OCR, an
unconfigured device) never cause NOT READY.

## utf --dry-run

Validates everything a real run would use — configuration, every test
definition, assets, preflight, devices, screenshots, instrumentation,
backend connectivity — and **executes nothing**: no state changes, no test
steps, no input events.

```bash
utf --dry-run
utf run --dry-run        # equivalent
```

## utf list

```bash
utf list
utf list --feature movies
utf list --tag smoke --platform yocto
```

Output groups tests by feature:

```text
Movies
  MOV-001    Movie artwork appears  (android, yocto)
  MOV-002    Movie title appears    (android, yocto)
```

## utf init

Creates a user configuration file from a commented template and prints its
platform-appropriate location. Refuses to overwrite an existing file unless
`--force` is given.

## utf update

After `git pull`: reinstalls the package and dependencies into the existing
environment, re-runs framework validation, and never touches your user
configuration.

## utf version / utf --version

Prints the framework version.

## Logging options

```bash
utf run --verbose            # DEBUG level
utf run --quiet              # errors only, minimal console output
utf run --log-level DEBUG    # explicit level
```

Log format and optional JSON log file are configured under `logging:` —
see [configuration.md](configuration.md).
