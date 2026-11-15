"""The samples as static JSON for visitors who are not signed in (udebt/hosted/static_samples.py),
derived from the committed load scripts: the loader reads back what it wrote, the committed
files in web/public/samples/ are current, and the index copes with a registered sample that
has no script or no advance quizzes yet."""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest

from udebt.hosted import static_samples
from udebt.hosted.samples import SAMPLES
from udebt.loadsql import (
    LoadError,
    analysis_json,
    analysis_rows,
    analysis_sql,
    json_from_sql,
    rows_from_sql,
)

ROOT = Path(__file__).resolve().parent.parent
DM = ROOT / "tests" / "fixtures" / "hosted" / "digitalme"
URL = "https://github.com/pisanuw/digitalme"


@pytest.mark.parametrize("dialect", ["postgres", "sqlite"])
@pytest.mark.parametrize(
    "kw",
    [
        {"public": True, "url": URL, "license": "MIT"},
        {"user_id": str(uuid.UUID(int=7)), "license": "it's MIT"},
        {"storage_path": "x/y.json", "with_export": False},
    ],
)
def test_the_loader_reads_back_what_it_wrote(dialect, kw):
    sql = analysis_sql(DM, dialect=dialect, **kw)
    assert rows_from_sql(sql) == json.loads(json.dumps(analysis_rows(DM, **kw)))
    assert json_from_sql(sql) == analysis_json(DM, **kw)


def test_a_script_it_cannot_read_is_refused():
    for bad in ("select 1;", "insert into app_udebt.repositories (url) values ($u1$x$u1$);",
                "insert into app_udebt.repositories (url, name) values ($u1$x$u1$);",
                "insert into app_udebt.repositories (url) values ($u1$x);", "x $ y",
                "insert into app_udebt.repositories (url) values ('x);"):  # fmt: skip
        with pytest.raises(LoadError):
            rows_from_sql(bad + "\ninsert into app_udebt.analyses (sha) values (null);")


def test_the_committed_static_samples_are_current():
    assert static_samples.stale(ROOT / "samples", ROOT / "web" / "public" / "samples") == [], (
        "web/public/samples/ is stale: python -m udebt.hosted.static_samples"
    )
    index = json.loads((ROOT / "web" / "public" / "samples" / "index.json").read_text())
    assert [s["name"] for s in index["samples"]] == list(SAMPLES)
    assert {s["sha"] for s in index["samples"]} == {s["sha"] for s in SAMPLES.values()}


def _quizzes(name: str, **over) -> dict:
    s = SAMPLES[name]
    q = {"unit_id": "u", "level": 1, "question": "What is it for?", "reference_answer": "x"}
    return {"format": "udebt-advance-quizzes", "version": 1, "repo": s["url"], "sha": s["sha"],
            "questions": [q, {**q, "level": 2}], **over}  # fmt: skip


def test_a_sample_without_a_script_or_quizzes_is_listed_without_them(tmp_path, capsys):
    sql, out = tmp_path / "sql", tmp_path / "out"
    sql.mkdir()
    out.mkdir()
    shutil.copy(ROOT / "samples" / "mlflow.sql", sql)
    (out / "mlflow.quizzes.json").write_text(json.dumps(_quizzes("mlflow")))
    assert static_samples.main(["--sql", str(sql), "--out", str(out)]) == 0
    index = json.loads((out / "index.json").read_text())
    by = {s["name"]: s for s in index["samples"]}
    assert by["mlflow"]["analysis"] == "mlflow.json" and by["mlflow"]["units"] == 30
    assert by["mlflow"]["quizzes"] == "mlflow.quizzes.json" and by["mlflow"]["questions"] == 2
    assert by["huggingface_hub"]["analysis"] is None and by["huggingface_hub"]["quizzes"] is None
    assert by["understanding-debt"]["license"] is None
    assert sorted(p.name for p in out.iterdir()) == ["index.json", "mlflow.json",
                                                     "mlflow.quizzes.json"]  # fmt: skip
    doc = json.loads((out / "mlflow.json").read_text())
    assert doc["analyses"][0]["sha"] == SAMPLES["mlflow"]["sha"]
    assert static_samples.main(["--sql", str(sql), "--out", str(out), "--check"]) == 0
    (out / "stray.json").write_text("{}")
    (out / "mlflow.json").write_text("{}")
    (out / "index.json").unlink()
    assert static_samples.main(["--sql", str(sql), "--out", str(out), "--check"]) == 1
    err = capsys.readouterr().err
    assert "mlflow.json: stale" in err and "index.json: missing" in err and "stray.json" in err


@pytest.mark.parametrize(
    "bad",
    [
        _quizzes("mlflow", sha="0" * 40),
        _quizzes("mlflow", repo="https://github.com/x/y"),
        _quizzes("mlflow", version=2),
        {"format": "udebt-advance-quizzes"},
    ],
)
def test_quizzes_off_the_pin_or_malformed_are_refused(tmp_path, bad, capsys):
    (tmp_path / "mlflow.quizzes.json").write_text(json.dumps(bad))
    assert static_samples.main(["--sql", str(tmp_path / "none"), "--out", str(tmp_path)]) == 1
    assert "mlflow.quizzes.json" in capsys.readouterr().err
    (tmp_path / "mlflow.quizzes.json").write_text("{not json")
    with pytest.raises(static_samples.StaticError, match="not JSON"):
        static_samples.build(tmp_path / "none", tmp_path)


def test_a_script_off_the_pin_or_not_shared_is_refused(tmp_path, capsys):
    (tmp_path / "mlflow.sql").write_text(analysis_sql(DM, public=True, url=URL))
    assert static_samples.main(["--sql", str(tmp_path), "--out", str(tmp_path / "o")]) == 1
    assert "the pin is https://github.com/mlflow/mlflow" in capsys.readouterr().err
    m = json.loads((DM / "analysis.json").read_text())
    own = tmp_path / "own"
    shutil.copytree(DM, own)
    (own / "analysis.json").write_text(json.dumps({**m, "sha": SAMPLES["mlflow"]["sha"]}))
    (tmp_path / "mlflow.sql").write_text(analysis_sql(own, public=True, url=SAMPLES["mlflow"][
        "url"], user_id=str(uuid.UUID(int=1))))  # fmt: skip
    with pytest.raises(static_samples.StaticError, match="not a shared sample"):
        static_samples.build(tmp_path, tmp_path / "o")
