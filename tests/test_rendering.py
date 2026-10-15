from datetime import datetime, timezone
from pathlib import Path

from job_research.model import CompanyNote, Job, Page
from job_research.service import render_job, render_research, render_research_page


def test_research_document_has_provenance_and_original_text(tmp_path: Path):
    company = CompanyNote("Acme", tmp_path / "Acme.md", {}, "")
    page = Page("https://acme.test/values", "Values", "## Be kind\n\nOriginal words.", "Be kind Original words", category="Mission & Values")
    page_path = tmp_path / "Acme - Company Research - values.md"
    result = render_research(company, [page], [page_path], [], datetime(2026, 10, 15, tzinfo=timezone.utc), tmp_path)
    assert "source-urls:" in result
    assert "https://acme.test/values" in result
    assert "content-sha256" in result
    assert "Original words." not in result
    source = render_research_page(company, page, datetime(2026, 10, 15, tzinfo=timezone.utc))
    assert "Original words." in source


def test_job_document_uses_stable_id():
    company = CompanyNote("Acme", Path("Acme.md"), {}, "")
    job = Job("greenhouse", "123", "Backend Engineer", "https://acme.test/jobs/123", "New York", description_markdown="Build systems.")
    result = render_job(company, job, datetime(2026, 10, 15, tzinfo=timezone.utc))
    assert "job-id: '123'" in result
    assert "Build systems." in result

