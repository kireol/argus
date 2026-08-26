# Monorepo Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Argus under `argus/`, import `../argus-test-creator` under `argus-test-creator/` with history, make the repo a uv workspace, and make the installers install both packages.

**Architecture:** Root holds only shared files (workspace `pyproject.toml`, installers, `action.yml`, CI workflow, umbrella README/CHANGELOG/LICENSE/CONTRIBUTING). Each project keeps its own `pyproject.toml`, `src/`, `tests/`, `docs/`, `examples/`. One shared `.venv` at the root.

**Tech Stack:** git (`git mv`, `git subtree add`), uv workspaces, hatchling, bash/PowerShell installers, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-26-monorepo-merge-design.md`

## Global Constraints

- Branch: `feature/monorepo` (already created). Base commits must use `git mv` so history/blame survives.
- Both packages become version `1.2.0`.
- The Creator never imports Argus; integration stays subprocess-only.
- `action.yml` stays at the repo root.
- Per-project `CHANGELOG.md`, `LICENSE`, `CONTRIBUTING.md` are removed; root copies are canonical.
- Python >= 3.12; existing `ruff`/`mypy` settings unchanged.

---

### Task 1: Move Argus under `argus/`

**Files:**
- Move (git mv): `src`, `tests`, `docs` (except `docs/superpowers/specs/2026-08-26-monorepo-merge-design.md` and `docs/superpowers/plans/2026-08-26-monorepo-merge.md`, which stay at root), `examples`, `config`, `test_suites`, `agents`, `assets`, `scripts`, `pyproject.toml`, `README.md`, `uv.lock` (untracked → delete; regenerated in Task 4)
- Stay at root: `install.sh`, `install.ps1`, `action.yml`, `.github/`, `CHANGELOG.md`, `LICENSE`, `CONTRIBUTING.md`, `.gitignore`, `.claude/`
- Modify: `argus/tests/unit/test_github_action.py` (root-relative paths)

- [ ] **Step 1: Move**

```bash
cd /Users/cparent/projects/argus
mkdir argus
git mv src tests examples config test_suites agents assets scripts pyproject.toml README.md argus/
mkdir -p argus/docs/superpowers
git mv docs/*.md argus/docs/
git mv docs/superpowers/plans docs/superpowers/specs argus/docs/superpowers/   # then move the two monorepo docs back
mkdir -p docs/superpowers/specs docs/superpowers/plans
git mv argus/docs/superpowers/specs/2026-08-26-monorepo-merge-design.md docs/superpowers/specs/
mv argus/docs/superpowers/plans/2026-08-26-monorepo-merge.md docs/superpowers/plans/ 2>/dev/null || true
rm -f uv.lock; mv results argus/results
```

- [ ] **Step 2: Fix root-relative test paths**

`argus/tests/unit/test_github_action.py`: add `REPO_ROOT = ROOT.parent`; read `action.yml` and `.github/workflows/argus-ci.yml` from `REPO_ROOT`; keep `examples/ci/github-workflow.yml` on `ROOT`.

- [ ] **Step 3: Verify Argus tests still pass from `argus/`**

Run: `cd argus && ../.venv/bin/python -m pytest -q -x tests/unit/test_github_action.py tests/unit/test_examples.py tests/unit/test_esp32_example_firmware.py`
Expected: pass (the old `.venv` still has argus installed editable at the old path — if import fails, run `../.venv/bin/pip install -q -e . ` first or `uv pip install -p ../.venv/bin/python -e .`).

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "Move Argus into argus/ for the monorepo layout"
```

### Task 2: Import argus-test-creator with history

- [ ] **Step 1: Subtree add**

```bash
git subtree add --prefix=argus-test-creator ../argus-test-creator main -m "Import argus-test-creator as a monorepo project (history preserved)"
```

- [ ] **Step 2: Verify**

Run: `git log --oneline | head -10 && ls argus-test-creator`
Expected: the five Creator commits appear in history; directory contains `pyproject.toml`, `src`, `tests`, `docs`.

### Task 3: Root shared files

**Files:**
- Create: `pyproject.toml` (workspace root)
- Modify: `.gitignore`, `CHANGELOG.md`, `CONTRIBUTING.md`, `README.md` (new umbrella)
- Delete: `argus-test-creator/CHANGELOG.md`, `argus-test-creator/LICENSE`, `argus-test-creator/CONTRIBUTING.md`, `argus-test-creator/.gitignore`
- Modify: `argus/pyproject.toml` version → `1.2.0`; `argus-test-creator/pyproject.toml` version → `1.2.0`, `argus = ["argus"]` extra keeps working via workspace source

- [ ] **Step 1: Root pyproject**

```toml
[tool.uv.workspace]
members = ["argus", "argus-test-creator"]

[tool.uv.sources]
argus = { workspace = true }
```

(uv requires a `[project]`-less root to be workspace-only; that is what we want — the root is not a package.)

- [ ] **Step 2: `.gitignore`** — union of both files; replace `results/` with `argus/results/`; add `.argus-creator/workspace/`; keep `agents/esp32/...` rule prefixed with `argus/`.

- [ ] **Step 3: `CHANGELOG.md`** — under `[Unreleased]` add a "Changed" entry for the monorepo move (paths, run from `argus/`, both installers), then a `## [1.2.0] - 2026-08-26` heading is NOT added yet (release flow adds it at bump). Append a `## Test Creator` section holding the Creator's changelog content.

- [ ] **Step 4: `CONTRIBUTING.md`** — dev setup via `./install.sh --dev` at root, then per-project gates (`cd argus && ../.venv/bin/...`; `cd argus-test-creator && scripts/dev.sh`). Merge the Creator's ground rules as a second section.

- [ ] **Step 5: Umbrella `README.md`** — overview, layout tree, install (both), links to `argus/README.md` and `argus-test-creator/README.md`, GitHub Action usage unchanged.

- [ ] **Step 6: Commit** `git add -A && git commit -m "Add uv workspace root, unified changelog/contributing, umbrella README"`

### Task 4: Installers

**Files:** `install.sh`, `install.ps1`

- [ ] **Step 1: `install.sh`** — `ARGUS_EXTRAS="yocto,ocr"`, `CREATOR_EXTRAS="ui"`; `--dev` appends `,dev` to both. Install: `uv pip install -p "$VENV_DIR/bin/python" -e "./argus[${ARGUS_EXTRAS}]" -e "./argus-test-creator[${CREATOR_EXTRAS}]"`; pip fallback identical. Launchers for `argus` and `argus-test-creator`. `mkdir -p argus/results`. Health check adds `"$VENV_DIR/bin/argus-test-creator" --help`. Summary lists both commands; demo run `cd argus && argus run --config config/fake.yaml`.
- [ ] **Step 2: `install.ps1`** — same changes; second launcher `argus-test-creator.cmd`.
- [ ] **Step 3: Run** `rm -rf .venv && ./install.sh --dev` → expect INSTALLATION COMPLETE, `.venv/bin/argus version`, `.venv/bin/argus-test-creator --help` succeed. Then `uv lock` to produce root `uv.lock`; commit it.
- [ ] **Step 4: Commit** `git add -A && git commit -m "Installers: install argus and argus-test-creator into one venv"`

### Task 5: CI, action, and path fixes

**Files:** `action.yml`, `.github/workflows/argus-ci.yml`, `argus-test-creator/tests/conftest.py:75`, `argus-test-creator/scripts/dev.sh`, `argus-test-creator/scripts/build-package.sh`, `argus-test-creator/docs/packaging.md`

- [ ] **Step 1: `action.yml`** — `pip install -q "${ARGUS_ACTION_PATH}/argus[...]"` / `"${ARGUS_ACTION_PATH}/argus"`; comment note that the action lives at the root of a monorepo.
- [ ] **Step 2: Workflow** — `pip install -q -e "./argus[dev]"`; `python -m pytest argus/tests/ci argus/tests/unit/test_github_action.py -q` (run from root; add `working-directory: argus` for the pytest step so `tests/` relative paths in conftest work); `config: argus/examples/ci/argus-ci.yml`; `extends: ${{ github.workspace }}/argus/config/fake.yaml`. Add job `creator-unit`: install `./argus-test-creator[dev]`, `QT_QPA_PLATFORM=offscreen`, `python -m pytest tests/unit -q` with `working-directory: argus-test-creator`.
- [ ] **Step 3: Creator conftest** — candidate `Path(__file__).resolve().parents[2] / ".venv" / "bin" / "argus"`.
- [ ] **Step 4: Creator scripts** — `.venv/` → `../.venv/` (three places in dev.sh, one in build-package.sh).
- [ ] **Step 5: Verify** — `cd argus && ../.venv/bin/python -m pytest -q tests/unit/test_github_action.py`; `cd argus-test-creator && scripts/dev.sh` (full gate, including `test_schema_catalog_in_sync_with_installed_argus`).
- [ ] **Step 6: Commit** `git commit -am "Update CI, action, and Creator scripts for the monorepo layout"`

### Task 6: Docs and version bump

**Files:** `argus/README.md`, `argus/docs/installation.md`, `argus/docs/getting-started.md`, `argus/docs/mcp.md`, `argus/examples/backend/README.md`, `argus/examples/desktop/README.md`, `argus-test-creator/README.md`, `argus-test-creator/docs/getting-started.md`, `argus-test-creator/docs/integrations.md`, `argus-test-creator/docs/architecture.md`, `argus-test-creator/docs/packaging.md`, both `pyproject.toml` versions.

- [ ] **Step 1** — Argus docs: installer is run from the repo root (`../install.sh` when in `argus/`, or `./install.sh` at root); `.venv` is `../.venv` relative to `argus/`; `argus run --config config/fake.yaml` examples note "from `argus/`".
- [ ] **Step 2** — Creator docs: installation section replaced with "installed by the root `install.sh`/`install.ps1`"; manual install `uv pip install --python ../.venv/bin/python -e ".[dev,ocr,browser]"`; discovery text `<repo>/.venv/bin/argus`.
- [ ] **Step 3** — Versions to `1.2.0` in both pyprojects; `argus/src/argus/__init__.py` if it hardcodes a version (check `grep -rn "1.1.11" argus argus-test-creator`).
- [ ] **Step 4** — Full verification: `cd argus && ../.venv/bin/ruff check src tests && ../.venv/bin/mypy src && ../.venv/bin/python -m pytest -q`; `cd argus-test-creator && scripts/dev.sh`.
- [ ] **Step 5: Commit** `git commit -am "Docs and version 1.2.0 for the monorepo"`

### Task 7: Old repository banner

- [ ] **Step 1** — In `../argus-test-creator`, prepend to `README.md`: `> **Moved:** this project now lives in the [kireol/argus](https://github.com/kireol/argus) monorepo under `argus-test-creator/`. This repository is archived.` Commit: `git commit -am "Point to the argus monorepo"`. Do not push.

### Task 8: Finish

- [ ] Push branch, open PR to `main` with summary; update memory files (`argus-test-creator-project.md`, `argus-dev-environment.md`) for the new locations.
