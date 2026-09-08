# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Independent re-verification of the three claimed issues (review only, read-only)."""
import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "movie_narrator"


def read(p):
    return (SRC / p).read_text(encoding="utf-8")


print("=" * 70)
print("ISSUE 1: mega-files + CLI option copy-paste")
print("=" * 70)
for p in ["cli.py", "cloud/api.py", "pipeline/match.py", "pipeline/render.py",
          "pipeline/runner.py", "pipeline/script.py", "cloud/queue.py"]:
    print(f"  {p:24s} {len(read(p).splitlines()):5d} lines")

raw = read("cli.py")
opts = re.findall(r"typer\.Option\(", raw)
print(f"  cli.py typer.Option( occurrences        : {len(opts)}")
_DEFFIND = re.compile(r"^\s*def ", re.M)
print(f"  cli.py 'def ' function definitions     : {len(_DEFFIND.findall(raw))}")

# Parse with AST, focus on create/race/imitate/submit command functions
tree = ast.parse(raw)
targets = {}
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef):
        args = node.args
        pos = args.posonlyargs + args.args
        # defaults apply to the trailing len(defaults) args
        defaults = {a.arg: d for a, d in zip(pos[-len(args.defaults):], args.defaults)}
        ocount = 0
        param_flags = {}
        for a in pos:
            d = defaults.get(a.arg)
            if isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "Option":
                ocount += 1
                kw = {}
                for k in d.keywords:
                    try:
                        kw[k.arg] = ast.literal_eval(k.value) if k.value else None
                    except Exception:
                        kw[k.arg] = "<...>"
                param_flags[a.arg] = kw
        if re.match(r"^(create|race|imitate|submit)$", node.name):
            targets[node.name] = (ocount, param_flags)

for name, (ocount, flags) in targets.items():
    print(f"  command {name:8s}: {ocount} typer.Option")

# flag drift: same flag name across the four commands with differing help/default
from collections import defaultdict
flagmap = defaultdict(dict)
for name, (ocount, flags) in targets.items():
    for param, kw in flags.items():
        flagmap[param][name] = kw
drift = []
completelist = {}
for param, bycmd in flagmap.items():
    if len(bycmd) >= 2:
        completelist.setdefault(("flag", param), set()).update(bycmd.keys())
# identify exact-copy dup vs drift
exact_defs = defaultdict(list)
for param, bycmd in flagmap.items():
    for name, kw in bycmd.items():
        key = (kw.get("default"), kw.get("full_names") or kw.get("names"),
               kw.get("help"))
        exact_defs[key].append((param, name))
exact_dup = [k for k, v in exact_defs.items() if len(v) >= 2]
print(f"  11+ param names shared across >=2 of the 4 cmds: {len([p for p in flagmap if len(flagmap[p])>=2])}")
print(f"  exact 4-tuples duplicated (identical flag+help+default): {len(exact_dup)}")

same_name_diff = 0
for param, bycmd in flagmap.items():
    if len(bycmd) < 2:
        continue
    forms = set()
    for name, kw in bycmd.items():
        forms.add((kw.get("default"), kw.get("help")))
    if len(forms) > 1:
        same_name_diff += 1
        if param in ("voice", "output_dir"):
            print(f"    drift  --{param}: { {n: (w.get('default'), w.get('help')) for n, w in bycmd.items()} }")
print(f"  same-named flag with help/default drift across cmds: {same_name_diff}")

# movie required (Ellipsis) check
if "submit" in targets:
    m = targets["submit"][1].get("movie") or {}
    print("  submit --movie default:", m.get("default"), "(Ellipsis = required)")

print("=" * 70)
print("ISSUE 2: Settings boundary drift")
print("=" * 70)
cfg = read("config.py")
# find class Settings fields with comments
field_lines = re.findall(r"^\s{4}([a-z_][a-z0-9_]*)\s*:\s*[^#]*(#.*)?$", cfg, re.M)
print(f"  config.py top-level field declarations (rough): {len(field_lines)}")
for kw in ["webhook", "rate_limit", "scheduler", "circuit", "distributed", "api_key",
           "api_principal", "rate_limit_enabled", "poll_interval"]:
    n = len(re.findall(re.escape(kw), cfg))
    print(f"      contains 'pause' is not; kw='{kw}': {n} hits")
# version comments
for m in re.finditer(r"v0\.8\.0|v0\.9\.\d|v1\.3\.1", cfg):
    pass
print("  version-comment hits:", len(re.findall(r"v0\.8\.0|v0\.9\.\d|v1\.3\.1", cfg)))
# docstring
ds = re.search(r'"""(.*?)"""', cfg, re.S)
if ds:
    head = ds.group(1).strip().splitlines()
    print("  docstring first 12 lines:")
    for l in head[:12]:
        print("     ", l.strip())

print("=" * 70)
print("ISSUE 3: MetadataDict weak typing")
print("=" * 70)
mod = read("models.py")
md = re.search(r"class MetadataDict\s*\(.*?\)\s*:\s*\n(.*?)(?=\nclass |\Z)", mod, re.S)
declared = set()
if md:
    for m in re.finditer(r"^\s{4}([A-Za-z_][A-Za-z0-9_]*)\s*:", md.group(1), re.M):
        declared.add(m.group(1))
print(f"  MetadataDict declared keys: {len(declared)}")
for k in ["_degraded_steps", "pause_at", "dry_run"]:
    print(f"      {k}: declared={k in declared}")
# scan source for string literal metadata keys
lit = {}
pat = re.compile(r"metadata\.get\(\s*['\"]([^'\"]+)['\"]|metadata\[['\"]([^'\"]+)['\"]|metadata\.get\(\s*f['\"]")
for p in (SRC).rglob("*.py"):
    t = p.read_text(encoding="utf-8")
    for mm in pat.finditer(t):
        k = mm.group(1) or mm.group(2)
        if k:
            lit[k] = lit.get(k, 0) + 1
print(f"  string-literal metadata keys scanned across src: {len(lit)}")
missing = {k: c for k, c in lit.items() if k not in declared}
print(f"  literal keys NOT in MetadataDict: {len(missing)}")
for k, c in sorted(missing.items(), key=lambda x: -x[1]):
    print(f"      '{k}' x{c}")