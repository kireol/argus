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

## Typical commands

`argus --help` ends with this list.

```bash
argus run --config config/2360x1300.yaml --no-logs --all --save-comparisons
    # every test against a config; hide INFO log lines; keep actual/expected/diff images
argus run --tag smoke --continue-on-failure   # smoke tests, keep going after failures
argus run -t MOV-001 -v                       # one test with DEBUG logging
argus run --config config/fake.yaml           # example suite on the fake devices
argus validate                                # installation, backend, every device
argus list --feature Movies                   # tests for one feature
argus ci run --suite pr                       # CI/CD-native run of a named suite
argus stress --scenario examples/stress/checkout-chaos.yaml   # monkey / chaos run
```

## argus mcp

Serve Argus to AI clients over the Model Context Protocol (optional
dependency: `pip install "argus[mcp]"`).

```bash
argus mcp                                        # stdio (Claude Code, IDEs)
argus mcp --config config/fake.yaml
argus mcp --transport streamable-http            # http://127.0.0.1:8000/mcp
argus mcp --transport streamable-http --host 0.0.0.0 --port 8765 --path /argus/mcp
```

Flags override the `mcp:` configuration section. Over stdio nothing but the
protocol is written to stdout (logs go to stderr). Exit code `2` for
configuration errors or a missing SDK. See [mcp.md](mcp.md).

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

## argus ci run

CI/CD-native execution over the same engine: provider detection (GitHub,
GitLab, Jenkins, Azure, generic, local), named suites, retry of transient
failures, quality gates, a deterministic `argus-results/` directory
(`report.json`, `junit.xml`, `report.html`, metadata, evidence), and GitHub
job summaries/annotations.

```bash
argus ci run                                # every test, provider auto-detected
argus ci run --suite pr                     # a suite from ci.suites
argus ci run --suite pr --tag player        # CLI selectors narrow the suite (AND)
argus ci run --suite nightly --workers 2    # parallel workers (device-partitioned)
argus ci run --retry 2 --fail-fast          # overrides for ci.retry / ci.execution
argus ci run --dry-run                      # resolve + validate, execute nothing
argus ci run --provider generic --no-report # skip provider publishing
argus ci run -o build/argus --no-artifacts  # custom output dir / no output dir
```

Exit codes: `0` success · `1` test failure · `2` configuration · `3`
environment · `4` test definition · `5` reporting · `6` policy · `7` internal
· `8` cancelled. Full reference: [ci-cd.md](ci-cd.md).

## argus stress

Monkey / stress / chaos testing over the same engine: randomized, screen-aware
UI actions, backend mutations and fault injection under a reproducible seed,
with structured failures, evidence, replay and minimization. Full reference:
[stress-testing.md](stress-testing.md).

```bash
argus stress                                        # stress: section of the config
argus stress --scenario examples/stress/checkout-chaos.yaml
argus stress --seed 84729163 --duration 10m         # deterministic, bounded
argus stress --dry-run                              # plan; every mutation blocked
argus stress --allow-destructive --stop-on-first    # opt in to delete/disable
argus stress list                                   # recorded runs
argus stress replay <run-id>                        # re-execute the recorded trace
argus stress minimize <run-id> --failure crash:crash  # shortest reproduction
```

Options: `--scenario`, `--seed`, `--device`, `--duration`, `--max-actions`,
`--dry-run`, `--allow-destructive`, `--stop-on-first/--continue`,
`--verbosity quiet|normal|verbose|debug|trace`, `--no-persist`.

Exit codes: `0` no error/critical application failure · `1` failures found ·
`2` configuration / unknown run · `3` infrastructure error · `130` cancelled.
Runs land in `results/stress/<run-id>/` (`run.json`, `trace.jsonl`,
`failures/<id>/`).

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
