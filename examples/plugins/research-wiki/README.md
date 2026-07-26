# movie-narrator-research-wiki

Example out-of-tree plugin demonstrating the `@register_research` provider
extension point in the v0.5 Plugin SDK.

## What it does

Registers a research provider `wiki` that fetches movie information from
Wikipedia's REST API instead of using an LLM. This is useful when:

- You don't have an LLM API key configured
- You want offline-friendly (free) movie metadata
- You want to demonstrate the Research provider extension point

## Installation

```bash
cd examples/plugins/research-wiki
pip install -e .
```

## Usage

### 1. Enable auto-discovery

```python
from movie_narrator import discover_plugins
discover_plugins()
```

Or load manually:

```python
from movie_narrator import load_plugin
from movie_narrator_research_wiki import WikiResearchPlugin
load_plugin(WikiResearchPlugin())
```

### 2. Select the wiki provider in your job config

```yaml
params:
  research_provider: wiki
```

Or via CLI:

```bash
mn create --movie "The Matrix" --research --config job.yaml
```

The pipeline will call Wikipedia's API instead of an LLM for the
`research_plot` step.

## How it works

1. The plugin registers a factory function with `research_registry`
2. When `research_plot` runs, it calls `research_registry.create("wiki", ctx, settings)`
3. The factory searches Wikipedia for the movie title, fetches the summary,
   and returns a `ResearchInfo` object
4. The pipeline writes `research.json` and uses the data for script generation

## Limitations

- Wikipedia summaries may not include cast or detailed plot information
- Year extraction is best-effort (regex on the summary text)
- Genre extraction is keyword-based and may miss or misidentify genres
- No API key required (uses public Wikipedia REST API)

## Comparison with built-in `llm` provider

| Feature | `llm` (built-in) | `wiki` (this plugin) |
|---------|-------------------|----------------------|
| Requires API key | Yes (OpenAI) | No |
| Summary quality | High (structured) | Medium (Wikipedia extract) |
| Cast/keywords | Yes | No |
| Genres | Yes (LLM-inferred) | Best-effort keyword match |
| Latency | 2-5s | 1-3s |
| Cost | Per-token | Free |
