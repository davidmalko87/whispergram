# Contributing

## Development setup

```bash
git clone https://github.com/davidmalko87/whispergram.git
cd whispergram
pip install ruff pytest        # the tests need no transcription deps
ruff check .
pytest -q
```

You do **not** need faster-whisper, ffmpeg, or a GPU to run the test suite — the mapping logic
is pure and the transcriber is injected as a fake in tests. Install `-r requirements.txt` only
when you want to actually transcribe audio.

## Privacy rule for contributors

Never include private chat data in the repo, an issue, or a PR — no real exports, transcripts,
`merged_chat.md`, audio files, or real names. The `.gitignore` blocks chat data by default; the
only sample committed is the synthetic fixture under `tests/fixtures/`. Run `git status` before
every commit, and keep any `--out` transcript paths **inside** an export folder.

## Versioning

This project uses [Semantic Versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`):

| Change type | Bump | Example |
|---|---|---|
| Backward-incompatible change | MAJOR | `1.x.x` → `2.0.0` |
| New backward-compatible feature | MINOR | `0.1.x` → `0.2.0` |
| Bug fix or small improvement | PATCH | `0.1.0` → `0.1.1` |

### How to bump the version

1. **Edit `whispergram.py`** — update the single source of truth:
   ```python
   __version__ = "0.1.1"   # update this line
   ```
2. **Update `pyproject.toml`** — set `version` to the same value.
3. **Add an entry to `CHANGELOG.md`** at the top of the file:
   ```markdown
   ## [0.1.1] - YYYY-MM-DD

   ### Fixed
   - Short description of the change.
   ```

All three must be updated together in the same commit as the change that warrants the bump.

## Publishing a release

1. Bump the version (above) and commit.
2. Push to `master`.
3. Create a GitHub Release with tag `vX.Y.Z` (the leading `v` matters), titled `vX.Y.Z`, with
   the new `CHANGELOG.md` section as the body.
4. Publishing the release triggers `.github/workflows/publish.yml`, which verifies the tag matches
   `__version__`, then builds and uploads to PyPI via trusted publishing. Do **not** mark it a
   pre-release or skip the `v`.
