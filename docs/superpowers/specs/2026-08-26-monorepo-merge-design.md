# Monorepo merge: argus-test-creator into argus — design

Date: 2026-08-26. Status: approved.

## Goal

Bring the sibling repository `argus-test-creator` into this repository as a second, independently
packaged project, preserving its git history, and make the installers install both packages.

## Layout

```
<repo root>
├── pyproject.toml              uv workspace root only (no package); members: argus, argus-test-creator
├── uv.lock                     single shared lockfile
├── .venv/                      single shared virtualenv (both packages installed editable)
├── install.sh / install.ps1    install both packages and both launchers
├── action.yml                  stays at root (required for `uses: kireol/argus@v1`); installs `${action_path}/argus`
├── .github/workflows/argus-ci.yml
├── README.md (umbrella) · CHANGELOG.md (unified) · LICENSE · CONTRIBUTING.md · .gitignore
├── argus/
│   ├── pyproject.toml          argus 1.2.0
│   ├── README.md, src/argus/, tests/, docs/, examples/, config/, test_suites/, agents/, assets/, scripts/
│   └── results/                gitignored, runtime output
└── argus-test-creator/
    ├── pyproject.toml          argus-test-creator 1.2.0
    └── README.md, src/argus_test_creator/, tests/, docs/, examples/, packaging/, scripts/
```

- Each project keeps its own README, docs, tests, examples and `docs/superpowers/{specs,plans}`.
- Per-project CHANGELOG / LICENSE / CONTRIBUTING are removed; the root copies are canonical. The
  Creator's CHANGELOG content is folded into the root CHANGELOG under a "Test Creator" heading.
- Root `.gitignore` is the union of both, with `argus/results/` and `.argus-creator/workspace/`.

## Git history

Branch `feature/monorepo`:

1. `git mv` every Argus file into `argus/` (rename detection keeps blame).
2. `git subtree add --prefix=argus-test-creator ../argus-test-creator main` — imports the
   Creator's commits verbatim.
3. Follow-up commits: workspace/installer/path fixes, version bump, docs.

Then PR → merge → tag per the usual release flow.

## Installers

`install.sh` / `install.ps1`:

- Create root `.venv`; install `./argus[yocto,ocr]` and `./argus-test-creator[ui]` editable
  (`--dev` / `-Dev` adds the `dev` extra of both). uv path and pip fallback both do two installs.
- Launchers: `~/.local/bin/argus` and `~/.local/bin/argus-test-creator`.
- Health check: `argus version`, `argus --help`, `argus validate --framework-only`,
  `argus-test-creator --help`.
- Result directory: `argus/results`.
- `argus update` (`src/argus/cli/main.py`) resolves `parents[3]` → `argus/`, which still has a
  `pyproject.toml`; unchanged.

## Path fixes

- `.github/workflows/argus-ci.yml`: `pip install -e ./argus[dev]`, `argus/tests/...`,
  `config: argus/examples/ci/argus-ci.yml`, `extends: ${{ github.workspace }}/argus/config/fake.yaml`;
  add a job running the Creator's unit tests (`pip install -e ./argus-test-creator[dev]`, offscreen Qt).
- `action.yml`: install `${ARGUS_ACTION_PATH}/argus[...]`.
- `argus/tests/unit/test_github_action.py`: `action.yml` and `.github/` read from `parents[3]`;
  `examples/ci/github-workflow.yml` from `parents[2]`.
- `argus-test-creator/tests/conftest.py`: default Argus path `<root>/.venv/bin/argus`.
- `argus-test-creator/scripts/dev.sh`, `build-package.sh`: `.venv` → `../.venv`.
- Docs (`installation.md`, `getting-started.md`, `ci-cd.md`, `mcp.md`, example READMEs, Creator
  `getting-started.md`/`integrations.md`/`packaging.md`): install and path references.

## Versioning

Both packages become `1.2.0`; root CHANGELOG gets a `1.2.0` entry describing the merge, the
Creator import, and the path change (run `argus` from `argus/` or pass `--config`; default
`test_paths: ["test_suites"]` is cwd-relative).

## Old repository

One commit on `../argus-test-creator` adds a "Moved to kireol/argus" banner to its README. The
user archives it on GitHub.

## Verification

- `./install.sh --dev` from a clean `.venv`; `argus version`; `argus-test-creator --help`.
- `argus/`: ruff, mypy, pytest (from `argus/`).
- `argus-test-creator/`: `scripts/dev.sh` (ruff, mypy, pytest incl. the schema-sync integration test
  against the shared venv's `argus`).
- `uv lock` succeeds; `test_github_action.py` validates workflow/action YAML.
