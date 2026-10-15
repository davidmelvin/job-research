from pathlib import Path

from job_research.model import CompanyNote
from job_research.notes import END, START, read_company, update_generated_links


def test_generated_section_is_idempotent_and_preserves_user_text(tmp_path: Path):
    path = tmp_path / "Example.md"
    original = """---
rating: 9
status: Interested
careers-url: https://example.test/careers
---

# Company overview

My personal opinion must survive.
"""
    path.write_text(original, encoding="utf-8")
    note = read_company(path)
    assert update_generated_links(note, "[[References/Example - Company Research]]", ["[[References/Example - Job 1]]"], False)
    first = path.read_text(encoding="utf-8")
    assert "My personal opinion must survive." in first
    assert first.count(START) == first.count(END) == 1
    note = read_company(path)
    assert not update_generated_links(note, "[[References/Example - Company Research]]", ["[[References/Example - Job 1]]"], False)
    assert path.read_text(encoding="utf-8") == first


def test_dry_run_does_not_write(tmp_path: Path):
    path = tmp_path / "Example.md"
    path.write_text("# Notes\n", encoding="utf-8")
    note = CompanyNote("Example", path, {}, "# Notes\n")
    assert update_generated_links(note, "[[Research]]", [], True)
    assert path.read_text(encoding="utf-8") == "# Notes\n"

