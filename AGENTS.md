# AGENTS.md

## Lint with ruff after every codegen step

After every code generation or edit step, run ruff and fix what it reports before moving on:

```bash
uv run ruff check .
uv run ruff format .
```

- There is no ruff config in this repo, so ruff's built-in defaults apply (ruff 0.17 enables a broad
  default set, e.g. `F401`, `PYI034`, `S110`, `BLE001`, `RUF059`, `UP047`, `EXE002`).
- **Unsafe fixes:** don't blindly run `--unsafe-fixes`. Preview first with
  `uv run ruff check . --fix --unsafe-fixes --diff`, then apply only the ones that don't change
  behavior. Check the leftovers by hand, since ruff can leave dead code behind (for example the unused
  `TypeVar` after `UP047`).
- `F401` in `src/ffox_test/__init__.py` is "unsafe" because an import there may be a public re-export.
  Before deleting one, check that nothing imports it from `ffox_test`.
- Fix errors rather than silencing them with `# noqa`. For `S110`/`BLE001`, narrow the exception types
  instead of catching `Exception`.
- `EXE002` (executable file with no shebang) can reappear on newly created files because the project
  directory's ACL grants group execute. Fix with `chmod a-x <file>`.
- The project targets Python 3.15 (`requires-python`), so modern syntax is fine: `typing.Self`,
  PEP 695 generics (`def f[T](...)`), and unparenthesized `except A, B:` (what `ruff format` produces).
