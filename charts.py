"""Renders validated chart specs to PNG. The LLM only chooses WHAT to plot; columns and values come from computed tables."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

INK, BLUE, AMBER, GREY = "#0F1B2D", "#2457F5", "#FFB400", "#DCE2EA"
COLORS = [BLUE, AMBER, INK]


def _fmt(v, _=None):
    a = abs(v)
    if a >= 1e9:
        return f"{v / 1e9:.1f}B"
    if a >= 1e6:
        return f"{v / 1e6:.1f}M"
    if a >= 1e4:
        return f"{v / 1e3:.0f}K"
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"


def table_df(table: dict) -> pd.DataFrame:
    return pd.DataFrame(table["rows"], columns=table["columns"])


def _with_x(spec, table: dict) -> pd.DataFrame:
    """Table as a DataFrame; x may be 'region + category' = those columns joined into one label."""
    df = table_df(table)
    parts = [p.strip() for p in spec.x.split(" + ")]
    if len(parts) > 1 and all(p in table["columns"] for p in parts) and spec.x not in table["columns"]:
        df[spec.x] = df[parts].astype(str).agg(" / ".join, axis=1)
    return df


def validate_spec(spec, table: dict) -> str | None:
    """Returns None if the spec can be drawn, else the reason."""
    cols = table["columns"]
    df = _with_x(spec, table)
    if spec.x not in df.columns:
        return f"x column '{spec.x}' not in the table"
    if spec.kind == "hist":
        return None if pd.to_numeric(df[spec.x], errors="coerce").notna().sum() >= 5 else "histogram needs a numeric column with >= 5 values"
    if not spec.y:
        return "no y column given"
    for y in spec.y[:3]:
        if y not in cols:
            return f"y column '{y}' not in the table"
        if pd.to_numeric(df[y], errors="coerce").notna().sum() == 0:
            return f"y column '{y}' is not numeric"
    if spec.kind == "scatter" and pd.to_numeric(df[spec.x], errors="coerce").notna().sum() < 3:
        return "scatter needs a numeric x column"
    return None


def render(spec, table: dict, path: Path) -> None:
    df = _with_x(spec, table)
    fig, ax = plt.subplots(figsize=(8, 4.4), dpi=150)
    ys = list(spec.y[:3])
    for c in ys:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if spec.kind in ("bar", "barh"):
        d = df.head(20)
        if spec.kind == "barh":
            d = df.sort_values(ys[0], ascending=True, na_position="last").head(20).iloc[::-1]
        pos = np.arange(len(d))
        w = 0.8 / len(ys)
        for i, y in enumerate(ys):
            off = (i - (len(ys) - 1) / 2) * w
            if spec.kind == "bar":
                ax.bar(pos + off, d[y], w * 0.92, label=y.replace('_', ' '), color=COLORS[i % 3])
            else:
                ax.barh(pos + off, d[y], w * 0.92, label=y.replace('_', ' '), color=COLORS[i % 3])
        labels = [str(v) for v in d[spec.x]]
        if spec.kind == "bar":
            ax.set_xticks(pos)
            ax.set_xticklabels(labels, rotation=30 if max(map(len, labels)) > 6 else 0, ha="right" if max(map(len, labels)) > 6 else "center")
            ax.yaxis.set_major_formatter(FuncFormatter(_fmt))
            ax.grid(axis="y", color=GREY, lw=0.8)
        else:
            ax.set_yticks(pos)
            ax.set_yticklabels(labels)
            ax.xaxis.set_major_formatter(FuncFormatter(_fmt))
            ax.grid(axis="x", color=GREY, lw=0.8)
    elif spec.kind == "line":
        for i, y in enumerate(ys):
            ax.plot(range(len(df)), df[y], marker="o", ms=4, lw=2, label=y.replace('_', ' '), color=COLORS[i % 3])
        step = max(1, len(df) // 10)
        ax.set_xticks(range(0, len(df), step))
        ax.set_xticklabels([str(v) for v in df[spec.x].iloc[::step]], rotation=30, ha="right")
        ax.yaxis.set_major_formatter(FuncFormatter(_fmt))
        ax.grid(axis="y", color=GREY, lw=0.8)
    elif spec.kind == "scatter":
        x = pd.to_numeric(df[spec.x], errors="coerce")
        ax.scatter(x, df[ys[0]], s=22, color=BLUE, alpha=0.8)
        ax.set_xlabel(spec.x)
        ax.set_ylabel(ys[0])
        ax.grid(color=GREY, lw=0.8)
    else:  # hist
        ax.hist(pd.to_numeric(df[spec.x], errors="coerce").dropna(), bins=20, color=BLUE, edgecolor="white")
        ax.set_xlabel(spec.x)
        ax.grid(axis="y", color=GREY, lw=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GREY)
    ax.tick_params(colors=INK, labelsize=9, length=0)
    import textwrap
    ax.set_title(textwrap.fill(spec.title, 58), loc="left", fontsize=13, fontweight="bold", color=INK, pad=14)
    if len(ys) > 1 and spec.kind != "hist":
        ax.legend(frameon=False, fontsize=9)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor="white")
    plt.close(fig)
