"""Unit test cases for pipeline."""

from pathlib import Path

import pytest

from cgis.extractors.python_extractor import PythonExtractor
from cgis.pipeline import IngestionPipeline


@pytest.fixture
def pipeline() -> IngestionPipeline:
    return IngestionPipeline({".py": PythonExtractor()})


def test_pipeline_extracts_function(pipeline: IngestionPipeline, tmp_path: Path) -> None:
    (tmp_path / "calc.py").write_text("def calc_sum(a, b):\n    return a + b\n", encoding="utf-8")

    nodes, raw_edges, _resolved = pipeline.run(str(tmp_path))

    assert len(nodes) == 1
    assert nodes[0].name == "calc_sum"
    assert len(raw_edges) == 0


def test_pipeline_raises_for_nonexistent_path(pipeline: IngestionPipeline) -> None:
    with pytest.raises(FileNotFoundError):
        pipeline.run("/nonexistent/path/that/does/not/exist")


def test_pipeline_raises_for_file_instead_of_dir(
    pipeline: IngestionPipeline, tmp_path: Path
) -> None:
    file_path = tmp_path / "not_a_dir.py"
    file_path.write_text("x = 1", encoding="utf-8")

    with pytest.raises(NotADirectoryError):
        pipeline.run(str(file_path))


def test_pipeline_skips_unknown_extensions(pipeline: IngestionPipeline, tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("some text", encoding="utf-8")
    (tmp_path / "script.py").write_text("def fn(): pass", encoding="utf-8")

    nodes, _, _ = pipeline.run(str(tmp_path))

    assert len(nodes) == 1
    assert nodes[0].name == "fn"


def test_pipeline_skips_excluded_dirs(pipeline: IngestionPipeline, tmp_path: Path) -> None:
    excluded = tmp_path / "node_modules"
    excluded.mkdir()
    (excluded / "ignored.py").write_text("def should_skip(): pass", encoding="utf-8")
    (tmp_path / "main.py").write_text("def main(): pass", encoding="utf-8")

    nodes, _, _ = pipeline.run(str(tmp_path))

    names = {n.name for n in nodes}
    assert "main" in names
    assert "should_skip" not in names


def test_pipeline_logs_and_skips_unparseable_file(
    pipeline: IngestionPipeline, tmp_path: Path
) -> None:
    (tmp_path / "broken.py").write_bytes(b"\xff\xfe invalid utf-8")
    (tmp_path / "good.py").write_text("def fine(): pass", encoding="utf-8")

    nodes, _, _ = pipeline.run(str(tmp_path))

    assert len(nodes) == 1
    assert nodes[0].name == "fine"
