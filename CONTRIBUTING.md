# Contributing to Nautilus Studio

Thanks for helping make long-form AI filmmaking accessible to creators.

## Development setup

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
nautilus-studio --host 127.0.0.1 --port 7860
```

The deterministic planner works without any external service. Configure model
endpoints only for the feature you are testing.

## Before opening a pull request

Run:

```bash
make check
```

Or run the individual checks:

```bash
ruff format --check src tests scripts/probe-image-edit.py scripts/probe-h3-continuation.py scripts/verify-qwen-image-edit-checkpoint.py
ruff check src tests scripts/probe-image-edit.py scripts/probe-h3-continuation.py scripts/verify-qwen-image-edit-checkpoint.py
pytest -q
node --check src/long_video_studio/static/app.js
```

For provider or media changes, include the exact endpoint type, model revision,
request shape, output artifact, and whether the test used real hardware or a
mock transport. Never include API keys, private registry credentials, or local
model paths in a commit.

## Change scope

- Keep planner, provider, and renderer contracts independent.
- New vendor integrations belong behind an adapter.
- Do not silently fall back after a configured provider fails.
- Preserve old projects when adding persisted fields.
- Add a focused regression test for every bug fix.

All commits contributed to the project should include a Developer Certificate
of Origin sign-off (`git commit -s`).
