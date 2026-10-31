"""Runs ONE piece of LLM-written analysis code in an isolated Python process.

Protocol: JSON on stdin {csv, date_cols, code}; JSON on the last stdout line.
Safety layers: AST whitelist (no imports, no dunder/underscore access, no file/network/exec helpers),
restricted builtins, CPU/memory limits, and a hard timeout enforced by the parent process.
"""
import ast
import json
import sys

import numpy as np
import pandas as pd

BANNED_NAMES = {"exec", "eval", "compile", "open", "__import__", "getattr", "setattr", "delattr", "globals",
                "locals", "vars", "input", "breakpoint", "help", "dir", "type", "object", "super",
                "memoryview", "bytearray", "exit", "quit", "classmethod", "staticmethod", "property"}
BANNED_ATTRS = {"to_csv", "to_excel", "to_json", "to_pickle", "to_sql", "to_parquet", "to_hdf", "to_feather",
                "to_clipboard", "to_html", "to_latex", "to_markdown", "to_xml", "to_orc", "to_stata",
                "eval", "format", "format_map", "system", "popen", "spawn", "load", "loads", "save", "savez",
                "savetxt", "fromfile", "tofile", "genfromtxt", "loadtxt", "memmap", "mro", "ctypeslib",
                "f2py", "testing", "show", "plot", "savefig"}
ALLOWED_TO = {"to_numeric", "to_datetime", "to_period", "to_timestamp", "to_frame", "to_dict", "to_list",
              "to_numpy", "to_string", "to_timedelta"}
BANNED_NODES = (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal, ast.ClassDef, ast.With, ast.AsyncWith,
                ast.AsyncFunctionDef, ast.AsyncFor, ast.Await, ast.Yield, ast.YieldFrom)
SAFE = ["abs", "all", "any", "bool", "dict", "enumerate", "float", "int", "len", "list", "max", "min", "range",
        "round", "set", "sorted", "str", "sum", "tuple", "zip", "isinstance", "reversed", "map", "filter",
        "pow", "divmod", "slice", "frozenset"]


def check_code(code: str) -> ast.AST:
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, BANNED_NODES):
            raise ValueError(f"not allowed: {type(node).__name__}")
        if isinstance(node, ast.Name) and (node.id in BANNED_NAMES or node.id.startswith("__")):
            raise ValueError(f"not allowed: name '{node.id}'")
        if isinstance(node, ast.Attribute):
            a = node.attr
            if a.startswith("_") or a in BANNED_ATTRS or a.startswith("read_") or (a.startswith("to_") and a not in ALLOWED_TO):
                raise ValueError(f"not allowed: attribute '{a}'")
    return tree


def to_table(res, limit=60):
    if isinstance(res, pd.DataFrame):
        t = res
    elif isinstance(res, pd.Series):
        t = res.to_frame(name=str(res.name) if res.name is not None else "value")
    elif isinstance(res, dict):
        t = pd.DataFrame({"metric": [str(k) for k in res], "value": list(res.values())})
    elif isinstance(res, (int, float, np.integer, np.floating, str, bool)):
        t = pd.DataFrame({"value": [res]})
    else:
        raise ValueError("`result` must be a DataFrame, Series, dict or scalar")
    if not isinstance(t.index, pd.RangeIndex):
        t = t.reset_index()
    t = t.copy()
    t.columns = [" ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in t.columns]
    n_total = len(t)
    t = t.head(limit)

    def cell(v):
        if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NaT:
            return None
        if isinstance(v, (pd.Timestamp,)):
            return v.strftime("%Y-%m-%d")
        if isinstance(v, (bool, np.bool_)):
            return bool(v)
        if isinstance(v, (int, np.integer)):
            return int(v)
        if isinstance(v, (float, np.floating)):
            return None if np.isnan(v) else round(float(v), 4)
        if isinstance(v, pd.Period):
            return str(v)
        return str(v)

    return {"columns": list(t.columns), "rows": [[cell(v) for v in r] for r in t.itertuples(index=False, name=None)],
            "n_rows_total": int(n_total)}


def main():
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (25, 25))
        resource.setrlimit(resource.RLIMIT_AS, (3 * 1024 ** 3, 3 * 1024 ** 3))
    except Exception:
        pass
    payload = json.loads(sys.stdin.read())
    try:
        tree = check_code(payload["code"])
        df = pd.read_csv(payload["csv"], keep_default_na=False, na_values=[""])
        for c in payload.get("date_cols", []):
            if c in df.columns:
                df[c] = pd.to_datetime(df[c], errors="coerce")
        ns = {"__builtins__": {k: __builtins__[k] if isinstance(__builtins__, dict) else getattr(__builtins__, k)
                               for k in SAFE},
              "pd": pd, "np": np, "df": df.copy()}
        exec(compile(tree, "<analysis>", "exec"), ns)
        if "result" not in ns:
            raise ValueError("code must assign a variable named `result`")
        out = {"ok": True, "table": to_table(ns["result"])}
    except Exception as e:
        out = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}
    sys.stdout.write("\n" + json.dumps(out))


if __name__ == "__main__":
    main()
