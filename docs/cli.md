# CLI Reference

```text
argus [GLOBAL OPTIONS] COMMAND [OPTIONS]
```

Global options (before the command): `--config/-c FILE`, `--log-level LEVEL`,
`--verbose/-v`, `--quiet/-q`, `--no-logs`, `--dry-run`, `--version`.
`--config` is also accepted on `run`, `list`, and `validate`.

`argus --help` and `argus run --help` both list filter / failure-policy flags
under **Run options** (`--test`, `--feature`, `--tag`, `--platform`, `--all`,
`--stop-on-failure` / `--continue-on-failure`, `--max-failures`,
`--skip-preflight`, `--skip-to`, `--save-comparisons`). Those flags work as
`argus run --feature movies` or `argus --feature movies run`.

## argus run

Run tests, filtered any way you like.

```bash
argus run                                   # everything
argus run --all                             # same, explicit
argus run --test MOV-001 --test MOV-002     # by ID
argus run --feature movies                  # by feature (case-insensitive)
argus run --tag smoke                       # by tag (repeatable, ANDed)
argus run --tag "smoke and not slow"        # boolean tag expression
argus run --platform android                # by platform
argus run --platform android --feature movies --tag smoke   # combined
argus run --skip-to 68                      # resume at console test number 68
argus run --save-comparisons                # keep image compare artifacts on pass too
argus run --no-logs                         # progress only; hide timestamped INFO lines
```

`--skip-to N` starts at the Nth test in the filtered suite (1-based),
matching the `N/M` progress shown in the console. Earlier tests are not
executed; progress still shows `68/70`, `69/70`, …

Failure policy (spec §24 — centralized, default stop-on-failure):

```bash
argus run --stop-on-failure        # default: stop at first failure
argus run --continue-on-failure    # run everything, report at the end
argus run --max-failures 5         # stop after N failures
```

Other flags: `--skip-preflight` (not recommended), `--dry-run`,
`--config FILE`, `--no-logs` (hide timestamped INFO lines like `shell.run`;
keep `→` / `✓` / `✗` progress), `--save-comparisons` (keep actual/expected/diff
images for image verifies so `report.html` can show them — also
`results.save_comparison_images: true` in config).

Exit codes: `0` all passed · `1` failures (or no tests matched) ·
`2` configuration/definition error · `3` preflight failed.

Reports land in `results/<timestamp>/`: `report.json`, `junit.xml`,
`report.html` (tests grouped by feature, with failure details and embedded
`actual` / `expected` / `diff` / screenshot images when present), plus per-test
artifact directories for failures.

## argus validate

Environment diagnosis, section by section, with
✓ (ok) / ⚠ (optional problem) / ✗ (blocking) / ○ (not configured):

```bash
argus validate                    # framework + backend + every device
argus validate --framework-only   # installation check, no hardware needed
```

Exit code `0` when READY, `3` otherwise. Optional components (OCR, an
unconfigured device) never cause NOT READY.

## argus --dry-run

Validates everything a real run would use — configuration, every test
definition, assets, preflight, devices, screenshots, instrumentation,
backend connectivity — and **executes nothing**: no state changes, no test
steps, no input events.

```bash
argus --dry-run
argus run --dry-run        # equivalent
```

## argus list

```bash
argus list
argus list --feature movies
argus list --tag smoke --platform yocto
```

Output groups tests by feature:

```text
Movies
  MOV-001    Movie artwork appears  (android, yocto)
  MOV-002    Movie title appears    (android, yocto)
```

## argus init

Creates a user configuration file from a commented template and prints its
platform-appropriate location. Refuses to overwrite an existing file unless
`--force` is given.

## argus update

After `git pull`: reinstalls the package and dependencies into the existing
environment, re-runs framework validation, and never touches your user
configuration.

## argus version / argus --version

Prints the framework version.

## Logging options

```bash
argus run --verbose            # DEBUG level
argus run --quiet              # errors only, minimal console output
argus run --no-logs            # keep progress; hide timestamped INFO lines
argus run --log-level DEBUG    # explicit level
```

Log format and optional JSON log file are configured under `logging:` —
see [configuration.md](configuration.md).
