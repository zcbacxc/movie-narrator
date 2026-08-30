# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Structural tests for the v1.5.2 Helm chart (deploy/helm/movie-narrator).

Honest scope: these are CI-safe *structural* tests — no helm binary is
required or used. Three checks:

1. Chart.yaml / values.yaml parse as YAML and carry the expected
   chart metadata (apiVersion v2, release appVersion).
2. Drift tripwire — every ``.Values.*`` key referenced by any template
   (including _helpers.tpl and NOTES.txt) must exist in values.yaml.
   This is the real regression guard against editing one side only.
3. Every ``templates/*.yaml`` renders to parseable YAML documents after a
   *naive* substitution pass. The renderer (``_NaiveRenderer``) is a
   miniature helm/mustache evaluator implementing only the constructs the
   chart actually uses (if/else/end, define/include, toYaml, nindent,
   quote, default, printf, trunc, trimSuffix, replace, randAlphaNum,
   .Values/.Release/.Chart lookups). The naivety is the point and is
   documented: full ``helm template`` / ``helm lint`` validation and a
   live-cluster smoke test did NOT happen at authoring time — no helm
   binary is available in the development environment (ADR-019). These
   structural tests are the CI-safe substitute; if a template uses a
   construct outside the evaluator below, add the construct (mirroring
   helm semantics) rather than weakening the YAML-parse check.
"""

import re
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[1] / "deploy" / "helm" / "movie-narrator"
TEMPLATES_DIR = CHART_DIR / "templates"

# Fixed release identity for the naive render (documented naivety — the
# real values come from `helm install -n <ns> <name>`).
RELEASE_NAME = "mn-release"
RELEASE_NAMESPACE = "default"


# ── Naive helm-subset renderer ────────────────────────────

_CHUNK = re.compile(r"\{\{(-)?\s*(.*?)\s*(-)?\}\}", re.DOTALL)
_OPENERS = ("if ", "with ", "range ", "block ", "define ")


def _tokenize(text: str):
    """Split template text into ("text"|"chunk", content) tokens and
    apply helm's ``{{-`` / ``-}}`` whitespace trimming."""
    tokens = []
    chunk_positions = []
    pos = 0
    matches = list(_CHUNK.finditer(text))
    for m in matches:
        if m.start() > pos:
            tokens.append(["text", text[pos:m.start()]])
        chunk_positions.append(len(tokens))
        tokens.append(["chunk", m.group(2)])
        pos = m.end()
    if pos < len(text):
        tokens.append(["text", text[pos:]])
    # helm whitespace control: {{- rstrips the nearest previous text
    # token, -}} lstrips the nearest following one. Pair regex matches
    # with chunk tokens in document order (identical contents repeat).
    for m, tok_i in zip(matches, chunk_positions):
        if m.group(1):
            for j in range(tok_i - 1, -1, -1):
                if tokens[j][0] == "text":
                    tokens[j][1] = tokens[j][1].rstrip()
                    break
        if m.group(3):
            for j in range(tok_i + 1, len(tokens)):
                if tokens[j][0] == "text":
                    tokens[j][1] = tokens[j][1].lstrip()
                    break
    return tokens


def _apply_trims(text: str, tokens):
    """Re-scan the raw text to recover the - flags, then trim neighbours."""
    for m in _CHUNK.finditer(text):
        left, right = bool(m.group(1)), bool(m.group(3))
        # Find the index of this chunk in tokens by matching content.
        content = m.group(2)
        for i, tok in enumerate(tokens):
            if tok[0] == "chunk" and tok[1] == content:
                if left and i > 0 and tokens[i - 1][0] == "text":
                    tokens[i - 1][1] = tokens[i - 1][1].rstrip()
                if right and i + 1 < len(tokens) and tokens[i + 1][0] == "text":
                    tokens[i + 1][1] = tokens[i + 1][1].lstrip()
                break
    return tokens


def _truthy(value) -> bool:
    """Helm truthiness: nil/""/false/empty list / empty map are false."""
    return bool(value)


class _NaiveRenderer:
    """Renders the chart's template subset (see module docstring)."""

    def __init__(self, values: dict, chart: dict) -> None:
        self.values = values
        self.chart = chart
        self.root = {
            "Values": values,
            "Release": {
                "Name": RELEASE_NAME,
                "Namespace": RELEASE_NAMESPACE,
                "Service": "Helm",
            },
            "Chart": chart,
        }
        self.defines: dict = {}
        helper = (TEMPLATES_DIR / "_helpers.tpl").read_text(encoding="utf-8").replace(
            "\r\n", "\n"
        )
        self._extract_defines(_tokenize(helper))

    # ── define extraction ─────────────────────────────

    def _extract_defines(self, tokens) -> None:
        """Populate self.defines from ``define "name"`` blocks."""
        i = 0
        while i < len(tokens):
            kind, content = tokens[i]
            m = re.match(r'^define\s+"([^"]+)"$', content)
            if kind == "chunk" and m:
                depth = 1
                body = []
                i += 1
                while i < len(tokens) and depth > 0:
                    k, c = tokens[i]
                    if k == "chunk":
                        if c.startswith(_OPENERS):
                            depth += 1
                        elif c == "end":
                            depth -= 1
                            if depth == 0:
                                break
                    body.append([k, c])
                    i += 1
                self.defines[m.group(1)] = body
            i += 1

    # ── token stream rendering ────────────────────────

    def render(self, text: str) -> str:
        return self._render_tokens(_tokenize(text.replace("\r\n", "\n")))

    def _render_tokens(self, tokens) -> str:
        out = []
        # Each frame: [parent_emitting, emitting, branch_taken]
        stack = []

        def emitting() -> bool:
            return all(frame[1] for frame in stack)

        for kind, content in tokens:
            if kind == "text":
                if emitting():
                    out.append(content)
                continue
            if content.startswith("/*"):
                continue  # comment
            m_if = re.match(r"^if\s+(.+)$", content, re.DOTALL)
            m_with = re.match(r"^with\s+(.+)$", content, re.DOTALL)
            if m_if or m_with:
                parent = emitting()
                if m_with:
                    raise AssertionError(
                        "chart templates must not use `with` (renderer subset)"
                    )
                cond = parent and _truthy(self._eval(m_if.group(1)))
                stack.append([parent, cond, cond])
            elif content == "else":
                frame = stack[-1]
                frame[1] = frame[0] and not frame[2]
                frame[2] = True
            elif content == "end":
                stack.pop()
            elif emitting():
                out.append(str(self._eval(content)))
        assert not stack, "unbalanced if/end in template"
        return "".join(out)

    # ── expression evaluation ─────────────────────────

    def _eval(self, expr: str):
        expr = expr.strip()
        if expr.startswith("not "):
            return not _truthy(self._eval(expr[4:]))
        stages = _split_top(expr, "|")
        value = self._eval_stage(stages[0], None)
        for stage in stages[1:]:
            value = self._eval_stage(stage, value)
        return value

    def _eval_stage(self, expr: str, piped):
        expr = expr.strip()
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(.*)$", expr, re.DOTALL)
        if m and m.group(1) in _FUNCTIONS:
            name, rest = m.group(1), m.group(2)
            args = [self._eval(a) for a in _split_args(rest)] if rest.strip() else []
            if piped is not None:
                args.append(piped)
            return _FUNCTIONS[name](self, *args)
        if piped is not None:
            raise AssertionError(f"cannot pipe into non-function atom: {expr!r}")
        return self._eval_atom(expr)

    def _eval_atom(self, expr: str):
        expr = expr.strip()
        if expr.startswith("(") and expr.endswith(")"):
            return self._eval(expr[1:-1])
        if len(expr) >= 2 and expr[0] == expr[-1] and expr[0] in "\"'":
            return expr[1:-1]
        if re.fullmatch(r"-?\d+", expr):
            return int(expr)
        if expr == ".":
            return self.root
        if expr.startswith("."):
            node = self.root
            for part in expr[1:].split("."):
                node = node[part]
            return node
        raise AssertionError(f"unsupported template atom: {expr!r}")

    # ── builtins used by the chart ────────────────────

    def _fn_include(self, name, _ctx=None):
        body = self.defines.get(name)
        assert body is not None, f"unknown define: {name}"
        return self._render_tokens([tok for tok in body])

    def _fn_to_yaml(self, value):
        return yaml.safe_dump(value, default_flow_style=False).strip()

    def _fn_nindent(self, n, value):
        return "\n" + self._fn_indent(n, value)

    def _fn_indent(self, n, value):
        pad = " " * int(n)
        return "\n".join(pad + line for line in str(value).split("\n"))

    def _fn_quote(self, value):
        return '"' + str(value).replace('"', '\\"') + '"'

    def _fn_default(self, *args):
        default_value, value = args[0], args[-1]
        return value if _truthy(value) else default_value

    def _fn_trunc(self, n, value):
        return str(value)[: int(n)]

    def _fn_trim_suffix(self, suffix, value):
        s = str(value)
        return s[: -len(suffix)] if suffix and s.endswith(suffix) else s

    def _fn_replace(self, old, new, value):
        return str(value).replace(old, new)

    def _fn_printf(self, fmt, *args):
        out = ""
        parts = str(fmt).split("%s")
        for i, part in enumerate(parts):
            out += part
            if i + 1 < len(parts):
                out += str(args[i])
        return out

    def _fn_rand_alpha_num(self, n):
        # Deterministic stand-in for randAlphaNum — the naive render must
        # be reproducible; real randomness only matters at install time.
        base = "naive-render-0123456789abcdefghijklmnopqrstuvwxyz"
        return (base * 4)[: int(n)]


_FUNCTIONS = {
    "include": _NaiveRenderer._fn_include,
    "toYaml": _NaiveRenderer._fn_to_yaml,
    "nindent": _NaiveRenderer._fn_nindent,
    "indent": _NaiveRenderer._fn_indent,
    "quote": _NaiveRenderer._fn_quote,
    "default": _NaiveRenderer._fn_default,
    "trunc": _NaiveRenderer._fn_trunc,
    "trimSuffix": _NaiveRenderer._fn_trim_suffix,
    "replace": _NaiveRenderer._fn_replace,
    "printf": _NaiveRenderer._fn_printf,
    "randAlphaNum": _NaiveRenderer._fn_rand_alpha_num,
}


def _split_top(expr: str, sep: str):
    """Split on sep at paren/quote nesting depth 0."""
    parts, depth, quote, buf = [], 0, "", []
    for ch in expr:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif ch == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return [p.strip() for p in parts]


def _split_args(expr: str):
    """Split function arguments on top-level whitespace."""
    args, depth, quote, buf = [], 0, "", []
    for ch in expr.strip():
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif ch.isspace() and depth == 0:
            if buf:
                args.append("".join(buf))
                buf = []
        else:
            buf.append(ch)
    if buf:
        args.append("".join(buf))
    return args


# ── Fixtures / helpers ────────────────────────────────────


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _load_yaml(path: Path) -> dict:
    data = yaml.safe_load(_read(path))
    assert isinstance(data, dict), f"{path.name} must be a YAML mapping"
    return data


def _renderer(values: dict | None = None) -> _NaiveRenderer:
    return _NaiveRenderer(
        values if values is not None else _load_yaml(CHART_DIR / "values.yaml"),
        _chart_metadata(),
    )


def _chart_metadata() -> dict:
    chart = _load_yaml(CHART_DIR / "Chart.yaml")
    return {
        "Name": chart["name"],
        "Version": chart["version"],
        "AppVersion": chart["appVersion"],
    }


def _template_files():
    return sorted(p for p in TEMPLATES_DIR.iterdir() if p.is_file())


# ── Tests: chart metadata ─────────────────────────────────


def test_chart_yaml_parses_with_expected_metadata():
    chart = _load_yaml(CHART_DIR / "Chart.yaml")
    assert chart["apiVersion"] == "v2"
    assert chart["name"] == "movie-narrator"
    assert chart["type"] == "application"
    assert chart["version"] == "0.1.0"
    assert chart["appVersion"] == "1.5.2"


def test_values_yaml_parses_with_core_defaults():
    values = _load_yaml(CHART_DIR / "values.yaml")
    assert values["replicaCount"] == 1
    assert values["image"]["pullPolicy"] == "IfNotPresent"
    assert values["service"]["port"] == 8765
    assert values["mn"]["tracing"]["enabled"] == "0"
    assert values["mn"]["tracing"]["exporter"] == "none"
    assert values["persistence"]["enabled"] is True
    assert "serve" in values["mn"]["serveArgs"]
    # Probes must target the real routes from cloud/api.py: /health + /ready.
    deployment = _read(TEMPLATES_DIR / "deployment.yaml")
    assert "path: /health" in deployment
    assert "path: /ready" in deployment


def test_template_files_exist():
    names = {p.name for p in _template_files()}
    assert {
        "_helpers.tpl",
        "deployment.yaml",
        "service.yaml",
        "serviceaccount.yaml",
        "pvc.yaml",
        "configmap.yaml",
        "secret.yaml",
        "NOTES.txt",
    } <= names


# ── Tests: values/template drift tripwire ─────────────────

_VALUES_REF = re.compile(r"\.Values\.([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)")


@pytest.mark.parametrize("tpl", _template_files(), ids=lambda p: p.name)
def test_every_referenced_values_key_exists(tpl):
    values = _load_yaml(CHART_DIR / "values.yaml")
    refs = sorted(set(_VALUES_REF.findall(_read(tpl))))
    assert refs, f"{tpl.name} references no .Values keys — dead template?"
    for dotted in refs:
        node = values
        for part in dotted.split("."):
            assert isinstance(node, dict) and part in node, (
                f"{tpl.name} references .Values.{dotted} "
                f"but values.yaml does not define it"
            )
            node = node[part]


# ── Tests: naive render produces parseable YAML ──────────


@pytest.mark.parametrize(
    "tpl", [p for p in _template_files() if p.suffix == ".yaml"], ids=lambda p: p.name
)
def test_rendered_template_is_parseable_yaml(tpl):
    rendered = _renderer().render(_read(tpl))
    docs = [d for d in re.split(r"(?m)^---\s*$", rendered) if d.strip()]
    assert docs, f"{tpl.name} rendered to an empty manifest"
    for doc in docs:
        parsed = yaml.safe_load(doc)
        if parsed is None:  # an entirely-skipped template body
            continue
        assert isinstance(parsed, dict), f"{tpl.name} produced a non-mapping manifest"
        assert parsed.get("apiVersion"), f"{tpl.name} manifest missing apiVersion"
        assert parsed.get("kind"), f"{tpl.name} manifest missing kind"


def test_rendered_deployment_contains_expected_resources():
    rendered = _renderer().render(_read(TEMPLATES_DIR / "deployment.yaml"))
    doc = yaml.safe_load(rendered)
    assert doc["kind"] == "Deployment"
    assert doc["metadata"]["name"] == f"{RELEASE_NAME}-movie-narrator"
    assert doc["spec"]["replicas"] == 1
    container = doc["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "movie-narrator:1.5.2"
    assert container["args"][0] == "serve"
    env = {e["name"]: e for e in container["env"]}
    assert env["MN_API_KEY"]["valueFrom"]["secretKeyRef"]["key"] == "api-key"
    assert env["MN_TRACING"]["value"] == "0"
    assert container["livenessProbe"]["httpGet"]["path"] == "/health"
    assert container["readinessProbe"]["httpGet"]["path"] == "/ready"


def test_rendered_secret_generates_key_when_unset():
    rendered = _renderer().render(_read(TEMPLATES_DIR / "secret.yaml"))
    doc = yaml.safe_load(rendered)
    assert doc["kind"] == "Secret"
    # randAlphaNum 32 is a deterministic stand-in in the naive renderer;
    # assert the shape (quoted in the manifest text, 32 chars when parsed)
    # rather than the exact value.
    key = doc["stringData"]["api-key"]
    assert len(key) == 32
    assert re.search(r'^\s*api-key: "[^"]+"$', rendered, re.MULTILINE)


def test_rendered_respects_disabled_optional_resources():
    values = _load_yaml(CHART_DIR / "values.yaml")
    values["serviceAccount"]["create"] = False
    values["config"]["enabled"] = False
    rendered = _renderer(values).render(_read(TEMPLATES_DIR / "serviceaccount.yaml"))
    assert rendered.strip() == ""
    rendered_cm = _renderer(values).render(_read(TEMPLATES_DIR / "configmap.yaml"))
    assert rendered_cm.strip() == ""
    deployment = yaml.safe_load(_renderer(values).render(_read(TEMPLATES_DIR / "deployment.yaml")))
    mounts = deployment["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]
    assert all(m["name"] != "config" for m in mounts)


def test_rendered_pvc_skipped_when_existing_claim_set():
    values = _load_yaml(CHART_DIR / "values.yaml")
    values["persistence"]["existingClaim"] = "my-claim"
    rendered = _renderer(values).render(_read(TEMPLATES_DIR / "pvc.yaml"))
    assert rendered.strip() == ""
    deployment = yaml.safe_load(
        _renderer(values).render(_read(TEMPLATES_DIR / "deployment.yaml"))
    )
    volumes = deployment["spec"]["template"]["spec"]["volumes"]
    claim = [v for v in volumes if v["name"] == "data"][0]
    assert claim["persistentVolumeClaim"]["claimName"] == "my-claim"


def test_rendered_notes_has_no_unresolved_directives():
    rendered = _renderer().render(_read(TEMPLATES_DIR / "NOTES.txt"))
    assert "{{" not in rendered
    assert RELEASE_NAME in rendered
    assert "MN_API_KEY" in rendered
