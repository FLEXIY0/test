from pathlib import Path

import pipeline.web as web


def test_dashboard_describes_available_script(tmp_path: Path, monkeypatch) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "episode.md").write_text(
        "# TITLE: A Remembered Place\n"
        "# DESCRIPTION: A sample production script.\n"
        "# TAGS: memories, history\n\n"
        "[IMAGE: old main street]\nA short narration lives here.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(web, "SCRIPTS_DIR", scripts_dir)
    monkeypatch.setattr(web, "OUTPUT_DIR", tmp_path / "output")

    page = web.render_dashboard()

    assert "A Remembered Place" in page
    assert "1</strong><span>Scripts ready" in page
    assert "1</strong><span>Visual scenes" in page
    assert "No renders yet" in page
