# Contributing to ReproPack

Thank you for your interest in improving ReproPack! This document describes the guidelines for effective contributions.

## How to Contribute

1. **Open an issue** before starting a large change to discuss the design.
2. **Fork** the repository and create a descriptive branch: `feature/clear-name` or `fix/bug-description`.
3. **Write clean code** following PEP8, with type hints and docstrings.
4. **Add tests** for all new code. Coverage must not decrease.
5. **Run pre-commit** before committing: `pre-commit run --all-files`.
6. **Update documentation** if your change modifies the API or behavior.
7. **Open a Pull Request** with a clear description of the problem, solution, and how to test it.

## Code Standards

- **PEP8** is mandatory (ruff + black).
- **Type hints** on all public functions and methods.
- **Docstrings** in Google style for all public classes and functions.
- **Comments** only when the logic is not obvious.
- **Descriptive names**: avoid cryptic abbreviations.
- **Error handling**: use specific exceptions, never bare `except:`.

## Tests

Run the full test suite with:

```bash
pytest -v --cov=src/repropack --cov-report=term-missing
```

- All new code must include unit tests.
- Tests must be independent and not depend on system state (use tmp_path, mocks, etc.).
- If you add integration functionality, document the environment requirements.

## Review Process

- PRs require at least one approval.
- CI must pass (tests, ruff, black, mypy).
- Respond to review comments in a reasonable timeframe.

## Code of Conduct

Be respectful and constructive. ReproPack is a collaborative project for the entire scientific community.
