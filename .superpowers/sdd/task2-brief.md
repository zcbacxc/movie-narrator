# Task 2 Brief


## Task 2: Settings + Environment Helper

Add 7 new settings fields and an environment collector utility.

### Requirements

1. Add to `config.py` Settings class:
   - `library_dir: Optional[str] = None`
   - `default_bgm: Optional[str] = None`
   - `research_enabled: bool = False`
   - `research_provider: str = "llm"`
   - `scene_threshold: float = 27.0`
   - `match_min_score: float = 0.25`
   - `export_clips_default: bool = True`

2. Create `utils/environment.py`:
   - `collect_environment()` -> dict with keys: `python`, `platform`, `ffmpeg`

3. Append to `.env.example`:
   ```dotenv
   # v0.2 optional
   # LIBRARY_DIR=
   # DEFAULT_BGM=
   # RESEARCH_ENABLED=false
   # RESEARCH_PROVIDER=llm
   # SCENE_THRESHOLD=27.0
   # MATCH_MIN_SCORE=0.25
   # EXPORT_CLIPS_DEFAULT=true
   ```

4. Create `tests/test_settings.py` with tests verifying the new fields exist and have correct defaults.

### Key interfaces
- `collect_environment()` returns dict with `python` (version string), `platform` (platform.platform()), `ffmpeg` (path or empty string)
- Settings uses pydantic-settings BaseSettings with SettingsConfigDict

