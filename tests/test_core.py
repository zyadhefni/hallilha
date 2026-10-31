import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ["LLM_BACKEND"] = "fake"
import pandas as pd
from fastapi.testclient import TestClient
from gen_data import make_dataset
from grounding import build_pool, ungrounded
from sandbox import run_code


def test_dataset_has_planted_problems():
    ds = make_dataset("sales", 1)
    assert ds.planted["duplicate_rows"] > 0 and len(ds.dirty) > len(ds.truth)


def test_sandbox_blocks_bad_code(tmp_path):
    p = tmp_path / "d.csv"; pd.DataFrame({"a": [1, 2]}).to_csv(p, index=False)
    for code in ["import os\nresult = df", "result = df.to_csv('x')", "result = open('x')", "result = df.__class__"]:
        assert run_code(str(p), [], code)["ok"] is False
    assert run_code(str(p), [], "result = df.groupby('a').size().reset_index(name='n')")["ok"]


def test_grounding_flags_invented_number():
    pool = build_pool([{"columns": ["x", "v"], "rows": [["a", 120.0], ["b", 80.0]]}])
    assert ungrounded(["Total is 200.0"], pool) == []
    assert ungrounded(["Total is 9999.0"], pool) != []


def test_pipeline_and_api():
    import server
    from pipeline import Engine, Options
    server._engine = Engine(server.get_llm(), Options(), root=Path(tempfile.mkdtemp()))
    c = TestClient(server.app)
    rid = c.post("/api/runs/sample", json={"domain": "support"}).json()["id"]
    import time
    for _ in range(12):
        for _ in range(60):
            s = c.get(f"/api/runs/{rid}").json()
            if s["status"] in ("review", "done", "failed"):
                break
            time.sleep(0.3)
        if s["status"] == "done":
            break
        assert s["status"] == "review", s
        assert c.post(f"/api/runs/{rid}/approve").status_code == 200
    assert s["status"] == "done"
    assert c.get(f"/api/runs/{rid}/report.html").status_code == 200
    assert c.get(f"/api/runs/{rid}/clean.csv").status_code == 200


def test_number_parsing_regional_formats():
    from dataprofile import to_number as f
    import math
    ok = {"SAR 1,234.50": 1234.5, "١٬٢٣٤٫٥٠ ر.س": 1234.5, "1.234,50 SAR": 1234.5, "12,5": 12.5, "(120.00)": -120, "1 234,50": 1234.5, "1,234": 1234}
    for k, v in ok.items():
        assert abs(f(k) - v) < 1e-9, k
    for k in ["ORD-00514", "E0001", "2026-03-25", "25/03/2026", "N/A", "abc"]:
        assert math.isnan(f(k)), k


def test_locale_and_null_datasets():
    ds = make_dataset("sales", 3, n=300, locale=True)
    assert ds.insight_tokens and ds.dirty["revenue"].astype(str).str.contains("٫|,\\d\\d SAR", regex=True).any()
    assert make_dataset("hr", 3, insight=False).insight_tokens == []
