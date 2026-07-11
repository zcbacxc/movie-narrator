# Contributing

## Development Setup

```bash
git clone https://github.com/zcbacxc/movie-narrator.git
cd movie-narrator
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest -v
```

## Code Style

- Follow the existing code style in each module
- Add tests for new pipeline steps
- Update `docs/ROADMAP.md` when adding features

## Commit Convention

- `feat:` — new feature
- `fix:` — bug fix
- `docs:` — documentation only
- `chore:` — maintenance, CI, tooling
- `refactor:` — code change that neither fixes a bug nor adds a feature

## Submitting Changes

1. Fork the repo and create a feature branch
2. Make your changes with tests
3. Run `pytest -v` and ensure all tests pass
4. Submit a pull request
