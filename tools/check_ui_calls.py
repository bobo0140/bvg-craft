"""Сверява всяко извикване от JS с подписа на метода в Python."""
import re, sys, inspect, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import paths
d = tempfile.mkdtemp(); paths.DATA = d; paths.CONFIG = os.path.join(d, "c.json"); paths.LOG = os.path.join(d, "l.txt")
from app.api import Api

js = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "app.js"), encoding="utf-8").read()
sigs = {n: inspect.signature(f) for n, f in inspect.getmembers(Api, inspect.isfunction)
        if not n.startswith("_")}

def split_args(s):
    depth, cur, out = 0, "", []
    for ch in s:
        if ch in "([{": depth += 1
        if ch in ")]}": depth -= 1
        if ch == "," and depth == 0: out.append(cur.strip()); cur = ""
        else: cur += ch
    if cur.strip(): out.append(cur.strip())
    return out

calls = []
for m in re.finditer(r'call\("(\w+)"((?:,[^;]*?)?)\)', js):
    calls.append((m.group(1), split_args(m.group(2).lstrip(",")), "call"))
for m in re.finditer(r'api\.(\w+)\(([^()]*)\)', js):
    calls.append((m.group(1), split_args(m.group(2)), "api"))
# динамичното извикване call(b.dataset.fn) — минава с нула аргументи
calls.append(("get_java", [], "call"))

bad = []
for name, args, kind in calls:
    if name not in sigs:
        bad.append(f"{name}: няма такъв метод в Python"); continue
    sig = sigs[name]
    params = [p for p in sig.parameters.values() if p.name != "self"]
    varargs = any(p.kind == p.VAR_POSITIONAL for p in params)
    required = [p for p in params if p.default is p.empty and p.kind == p.POSITIONAL_OR_KEYWORD]
    maxn = float("inf") if varargs else len([p for p in params if p.kind == p.POSITIONAL_OR_KEYWORD])
    n = len(args)
    if any(a == "undefined" for a in args):
        bad.append(f"{name}({', '.join(args)}): праща undefined → null")
    if n < len(required) or n > maxn:
        bad.append(f"{name}: JS праща {n}, Python приема {len(required)}–{maxn}")
    else:
        print(f"  ✓ {name}({', '.join(args)})")
# всяко undefined в аргументи на call(...) или api.X(...), включително динамично
for i, line in enumerate(js.splitlines(), 1):
    if re.search(r"\b(call|api\.\w+)\(", line) and "undefined" in line:
        bad.append(f"ред {i}: undefined в извикване → pywebview праща null")
print()
print("ПРОБЛЕМИ:", bad or "няма")

if bad:
    sys.exit(1)
