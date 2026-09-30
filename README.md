# onboarding

company onboarding workflow

## Use it

Needs [uv](https://docs.astral.sh/uv/) and Python 3.14+. `uv sync` fetches the
interpreter if you don't already have one.

| Command | |
| --- | --- |
| `uv sync` | create `.venv` and install dependencies |
| `uv run onboarding` | run the CLI |
| `uv run pytest` | run the tests |
| `uv run ruff check --fix .` | lint |
| `uv run ruff format .` | format |
| `uv run mypy` | type-check |
| `uvx pre-commit install` | check every commit, once per clone |

### Layout

| Path | |
| --- | --- |
| `src/onboarding/main.py` | `main()`, what the console script calls |
| `tests/test_main.py` | its test |
| `pyproject.toml` | dependencies, entry point, ruff, mypy and pytest config |
| `.pre-commit-config.yaml` | the commit hooks |
| `.idea/runConfigurations/` | the PyCharm run config, committed so it is shared |

### Dependencies

`uv add <pkg>` for runtime, `uv add --dev <pkg>` for tooling. Both write to
`uv.lock`, which is committed so every clone resolves to the same versions.

### Hooks

The pre-commit hooks shell out to `uv run ruff` and `uv run mypy` rather than
pinning versions of their own. There is one of each — the ones in the `dev`
group — so a hook can never disagree with what you run by hand. Bump them with
`uv add --dev ruff@latest mypy@latest` and both sides move together.

mypy runs in `strict` mode over `src`, `tests` and `init_project.py`. Loosen it
in the `[tool.mypy]` block if a dependency ships no stubs.
