from pathlib import Path

import json

from job_research import cli
from job_research.model import CompanyNote, Config


def make_config(root: Path, company_names: list[str]) -> Config:
    companies = root / "Companies"
    references = root / "References"
    companies.mkdir()
    references.mkdir()
    for name in company_names:
        (companies / f"{name}.md").write_text("---\nrating: 9\n---\n", encoding="utf-8")
    automation = root / "Automation"
    automation.mkdir()
    return Config(root, root, companies, references, automation, {}, {}, {})


def test_research_all_limit_processes_only_first_chunk(monkeypatch, tmp_path: Path, capsys):
    config = make_config(tmp_path, ["Alpha", "Bravo", "Charlie"])
    processed: list[str] = []

    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "research_company", lambda _config, company, **_: processed.append(company.name) or {"company": company.name})

    assert cli.main(["research-all", "--missing-only", "--limit", "2"]) == 0

    assert processed == ["Alpha", "Bravo"]
    output = capsys.readouterr().out
    assert '"selected": 3' in output
    assert '"processed": 2' in output
    assert '"remaining": 1' in output


def test_research_all_missing_only_repeats_with_next_unarchived_companies(monkeypatch, tmp_path: Path, capsys):
    config = make_config(tmp_path, ["Alpha", "Bravo", "Charlie"])
    processed: list[str] = []

    def research(_config, company: CompanyNote, **_):
        processed.append(company.name)
        (config.references_dir / f"{company.name} - Company Research.md").write_text("saved", encoding="utf-8")
        return {"company": company.name}

    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "research_company", research)

    assert cli.main(["research-all", "--missing-only", "--limit", "2"]) == 0
    assert cli.main(["research-all", "--missing-only", "--limit", "2"]) == 0

    assert processed == ["Alpha", "Bravo", "Charlie"]


def test_research_all_rejects_nonpositive_limit(monkeypatch, tmp_path: Path, capsys):
    config = make_config(tmp_path, ["Alpha"])
    monkeypatch.setattr(cli, "load_config", lambda _: config)

    assert cli.main(["research-all", "--limit", "0"]) == 1
    assert "--limit must be at least 1" in capsys.readouterr().err


def test_research_all_prints_company_progress_to_stderr(monkeypatch, tmp_path: Path, capsys):
    config = make_config(tmp_path, ["Alpha"])

    def research(_config, _company, *, progress, **_):
        progress("Discovering official website and research pages")
        progress("Collected 1 research page(s); preparing archive")
        return {"company": "Alpha"}

    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "research_company", research)

    assert cli.main(["research-all", "--limit", "1"]) == 0

    captured = capsys.readouterr()
    assert "[1/1 Alpha] Starting" in captured.err
    assert "[1/1 Alpha] Discovering official website and research pages" in captured.err
    assert "[1/1 Alpha] Finished" in captured.err
    assert '"company": "Alpha"' in captured.out


def test_research_all_persists_each_attempt_to_vault_ledger(monkeypatch, tmp_path: Path, capsys):
    config = make_config(tmp_path, ["Alpha", "Bravo"])
    monkeypatch.setattr(cli, "load_config", lambda _: config)

    def research(_config, company, **_):
        if company.name == "Alpha":
            return {"company": company.name, "status": "failed", "error": "board unavailable"}
        return {"company": company.name, "status": "ok", "jobs": {"matched": 2}}

    monkeypatch.setattr(cli, "research_company", research)

    assert cli.main(["research-all", "--include-jobs", "--limit", "2"]) == 0

    lines = (config.automation_dir / "job-research-runs.jsonl").read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    assert [record["company"] for record in records] == ["Alpha", "Bravo"]
    assert records[0]["status"] == "failed"
    assert records[0]["error"] == "board unavailable"
    assert records[1]["jobs"]["matched"] == 2
    assert "job-research-runs.jsonl" in capsys.readouterr().err


def test_show_failures_reads_only_failed_attempts(monkeypatch, tmp_path: Path, capsys):
    config = make_config(tmp_path, [])
    cli.append_run_ledger(config, [{"company": "Alpha", "status": "failed", "error": "blocked"}], command="research-all", options={}, dry_run=False)
    cli.append_run_ledger(config, [{"company": "Bravo", "status": "ok"}], command="research-all", options={}, dry_run=False)
    monkeypatch.setattr(cli, "load_config", lambda _: config)

    assert cli.main(["show-failures"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert [entry["company"] for entry in output["failures"]] == ["Alpha"]
    assert output["failures"][0]["error"] == "blocked"


def test_research_all_dry_run_does_not_write_run_ledger(monkeypatch, tmp_path: Path, capsys):
    config = make_config(tmp_path, ["Alpha"])
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "research_company", lambda _config, company, **_: {"company": company.name})

    assert cli.main(["research-all", "--limit", "1", "--dry-run"]) == 0

    assert not (config.automation_dir / "job-research-runs.jsonl").exists()
