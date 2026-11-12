"""Experiment comparison page (F9): payload from the F7 summary tables, safe embedding, import boundary."""

import ast
import json
import math
from pathlib import Path

import pandas as pd
import pytest

from marketsim.viz import compare

SEEDS = [1, 2, 3]


def _metrics(run_ids, collapse=()):
    rows = []
    for k, run_id in enumerate(run_ids):
        rows.append({"run_id": run_id, "day1": 0.1 * k, "corr_R": 0.8, "gap_days": k,
                     "lag": float("nan") if k == 0 else 0.5, "collapse": run_id in collapse,
                     "unknown_metric": 1.0})
    return pd.DataFrame(rows)


@pytest.fixture
def e5_dir(tmp_path):
    d = tmp_path / "e5"
    d.mkdir()
    rows = [{"run_id": f"r_{mix}_{h}_{s}", "seed": s, "agents.composition": mix, "agents.herding.h": h}
            for mix in ("trend", "mixed") for h in (0.0, 0.2) for s in SEEDS]
    pd.DataFrame(rows).to_csv(d / "summary.csv", index=False)
    _metrics([r["run_id"] for r in rows], collapse={"r_trend_0.2_3"}).to_csv(d / "p5_metrics.csv", index=False)
    pd.DataFrame([{"metric": "day1", "factor": "mix", "contrast": "trend − mixed", "at": "h=0.0", "pairs": 3,
                   "mean": 0.3, "ci_low": 0.1, "ci_high": 0.5, "clear": True, "clear (Bonferroni)": False}]).to_csv(
        d / "p5_contrasts.csv", index=False)
    return d


@pytest.fixture
def e7_dir(tmp_path, e5_dir):
    d = tmp_path / "e7"
    ids = []
    for share in (0, 50):
        sub = d / f"network{share}"
        sub.mkdir(parents=True)
        part = [f"n{share}_{s}" for s in SEEDS]
        ids += part
        pd.DataFrame({"run_id": part, "seed": SEEDS}).to_csv(sub / "summary.csv", index=False)
    ids += [f"r_mixed_0.2_{s}" for s in SEEDS]  # the E5 cell joins as the 30% column
    _metrics(ids).to_csv(d / "p7_metrics.csv", index=False)
    pd.DataFrame([{"metric": "corr_R", "contrast": "50% − 0%", "pairs": 3, "mean": -0.02, "ci_low": -0.03,
                   "ci_high": -0.01, "clear": True, "clear (Bonferroni)": True}]).to_csv(d / "p7_contrasts.csv",
                                                                                         index=False)
    return d


def test_e5_payload_has_every_run_in_its_cell(e5_dir):
    payload = compare.build_payload([compare.load_e5(e5_dir)])
    e = payload["experiments"][0]
    assert e["rows"] == ["mixed", "trend"] and e["cols"] == ["0.0", "0.2"]
    runs = e["runs"]
    assert len(runs["run_id"]) == 12 and sorted(set(runs["seed"])) == SEEDS
    k = runs["run_id"].index("r_trend_0.2_3")
    assert (runs["row"][k], runs["col"][k], runs["collapse"][k]) == (1, 1, True)
    assert sum(runs["collapse"]) == 1
    assert [m["key"] for m in e["metrics"]] == ["day1", "lag", "corr_R", "gap_days"]  # known metrics only, in order
    assert runs["lag"][0] is None  # NaN becomes null, the page stays valid JSON
    assert e["contrasts"] == [{"metric": "day1", "contrast": "trend − mixed", "at": "h=0.0", "factor": "mix",
                               "pairs": 3, "mean": 0.3, "lo": 0.1, "hi": 0.5, "clear": True, "bonferroni": False}]


def test_e7_payload_adds_the_e5_cell_as_the_30_percent_column(e5_dir, e7_dir):
    e = compare.build_payload([compare.load_e7(e7_dir, e5_dir)])["experiments"][0]
    assert e["cols"] == ["0", "30", "50"] and e["colLabels"] == ["0%", "30% (E5)", "50%"]
    assert e["runs"]["col"].count(1) == 3 and len(e["runs"]["run_id"]) == 9
    assert e["contrasts"][0]["bonferroni"] is True and e["contrasts"][0]["factor"] == "network share"
    without = compare.build_payload([compare.load_e7(e7_dir)])["experiments"][0]
    assert without["cols"] == ["0", "50"]


def test_page_embeds_the_payload_safely(e5_dir):
    experiment = compare.load_e5(e5_dir)
    experiment.runs.loc[0, "run_id"] = "</script><script>alert(1)</script>"
    experiment.title = "E5 <b>bold</b> — x"
    page = compare.render(compare.build_payload([experiment]), cdn=True)
    assert "</script><script>alert(1)" not in page
    assert "<title>Experiments E5 &lt;b&gt;bold&lt;/b&gt;</title>" in page
    data = page.split("const P = ", 1)[1].split(";\n", 1)[0]
    assert json.loads(data)["experiments"][0]["runs"]["run_id"][0].startswith("</script>")
    assert "/*PAYLOAD*/" not in page and "<!--PLOTLY-->" not in page


def test_main_writes_the_page_or_explains_what_is_missing(e5_dir, e7_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "render", lambda payload, cdn=False: json.dumps(payload))
    out = tmp_path / "page.html"
    compare.main(["--e5", str(e5_dir), "--e7", str(e7_dir), "-o", str(out)])
    assert [e["key"] for e in json.loads(out.read_text())["experiments"]] == ["e5", "e7"]
    with pytest.raises(SystemExit):
        compare.main(["--e5", str(tmp_path / "none"), "--e7", str(tmp_path / "none"), "-o", str(out)])


def test_reads_only_summary_tables():
    tree = ast.parse(Path(compare.__file__).read_text(encoding="utf-8"))
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not any(m and m.startswith("marketsim") for m in modules), modules
    assert all(n.level == 0 for n in ast.walk(tree) if isinstance(n, ast.ImportFrom))


def test_every_known_metric_has_a_label_and_a_finite_target():
    for key, (label, explanation, target) in compare.METRICS.items():
        assert label and explanation
        assert target in (None, "high", "low") or math.isfinite(target), key
