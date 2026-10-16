import base64
from pathlib import Path

import pytest

from job_research import gmail_intake as gi
from job_research.model import Config


def msg(mid="m1", date=gi.CUTOFF_MS, subject="Software engineer role", sender="Recruiter <recruiter@agency.example>", body="We are hiring at Acme Corp"):
    data = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    return {"id": mid, "threadId": "t1", "internalDate": str(date), "snippet": "snippet", "payload": {"mimeType": "text/plain", "headers": [{"name": "Subject", "value": subject}, {"name": "From", "value": sender}], "body": {"data": data}}}


class FakeRequest:
    def __init__(self, data): self.data = data
    def execute(self): return self.data


class FakeMessages:
    def __init__(self, messages): self.data = messages
    def list(self, **kwargs): return FakeRequest({"messages": [{"id": m["id"]} for m in self.data[:kwargs.get("maxResults", 500)]]})
    def get(self, id, **kwargs): return FakeRequest(next(m for m in self.data if m["id"] == id))


class FakeUsers:
    def __init__(self, messages, history_id="10"): self.ms = FakeMessages(messages); self.history_id = history_id
    def messages(self): return self.ms
    def history(self): return self
    def list(self, **kwargs): return FakeRequest({"historyId": self.history_id, "history": []})
    def getProfile(self, **kwargs): return FakeRequest({"historyId": self.history_id})


class FakeService:
    def __init__(self, messages): self.u = FakeUsers(messages)
    def users(self): return self.u


def config(tmp_path: Path):
    companies, refs, automation = (tmp_path / x for x in ("Companies", "References", "Automation"))
    for folder in (companies, refs, automation): folder.mkdir()
    return Config(tmp_path, tmp_path, companies, refs, automation, {}, {}, {})


def test_cutoff_and_mime_and_classification(tmp_path):
    store = gi.IntakeStore(tmp_path / "state.sqlite3")
    service = FakeService([msg("old", gi.CUTOFF_MS - 1), msg("new")])
    result = gi.sync_account(store, "a@gmail.com", service, commit=True)
    assert result.processed == 1 and result.skipped_before_cutoff == 1
    row = store.candidates()[0]
    assert row["key"] == "a@gmail.com:new"
    assert row["employer"] == "Acme Corp"
    assert row["classification"] == "direct_outreach"
    store.close()


def test_classifications_and_agency_ambiguity():
    assert gi.classify_message("Job alert", "jobs@site.com", "new jobs matching", {})[0] == "job_alert"
    assert gi.classify_message("Interview invitation", "hr@company.com", "schedule interview", {})[0] == "application_interview"
    assert gi.classify_message("Hello", "x@example.com", "unrelated", {})[0] == "unrelated"
    assert gi.classify_message("Engineer opportunity", "agent@agency.com", "on behalf of our client for a role", {}) == ("unresolved", None)


def test_stable_key_dedupe_and_independent_checkpoints(tmp_path):
    store = gi.IntakeStore(tmp_path / "state.sqlite3")
    service = FakeService([msg()])
    gi.sync_account(store, "personal@gmail.com", service, commit=True)
    gi.sync_account(store, "personal@gmail.com", service, commit=True)
    gi.sync_account(store, "work@gmail.com", service, commit=True)
    assert len(store.candidates()) == 2
    assert store.checkpoint("personal@gmail.com")[0] == "10"
    assert store.checkpoint("work@gmail.com")[0] == "10"
    store.close()


def test_dry_run_does_not_mutate(tmp_path):
    store = gi.IntakeStore(tmp_path / "state.sqlite3")
    result = gi.sync_account(store, "a@gmail.com", FakeService([msg()]), commit=False)
    assert result.processed == 1
    assert not store.candidates()
    assert store.checkpoint("a@gmail.com") == (None, None, None)
    store.close()


def test_expired_history_recovers_by_bounded_scan(tmp_path):
    class Expired(FakeUsers):
        def list(self, **kwargs):
            class E(Exception):
                resp = type("R", (), {"status": 404})()
            raise E()
    store = gi.IntakeStore(tmp_path / "state.sqlite3")
    store.set_checkpoint("a@gmail.com", "1"); store.commit()
    service = FakeService([msg()]); service.u = Expired([msg()])
    result = gi.sync_account(store, "a@gmail.com", service, commit=True, maximum=5)
    assert result.mode == "recovery" and result.processed == 1
    store.close()


class PagedMessages(FakeMessages):
    def __init__(self, pages, all_messages):
        self.pages = pages
        self.all = {item["id"]: item for item in all_messages}
    def list(self, **kwargs):
        token = kwargs.get("pageToken")
        ids, next_token = self.pages[token]
        return FakeRequest({"messages": [{"id": mid} for mid in ids], "nextPageToken": next_token})
    def get(self, id, **kwargs): return FakeRequest(self.all[id])


def test_initial_scan_resumes_page_and_promotes_history_only_when_complete(tmp_path):
    store = gi.IntakeStore(tmp_path / "state.sqlite3")
    class Users(FakeUsers):
        def __init__(self):
            self.ms = PagedMessages({None: (["one"], "p2"), "p2": (["two"], None)}, [msg("one"), msg("two")])
            self.history_id = "initial-10"
    service = FakeService([]); service.u = Users()
    first = gi.sync_account(store, "a@gmail.com", service, commit=True, maximum=1)
    assert first.remaining
    assert store.checkpoint("a@gmail.com") == ("initial-10", "p2", "initial")
    second = gi.sync_account(store, "a@gmail.com", service, commit=True, maximum=1)
    assert not second.remaining
    assert [row["message_id"] for row in store.candidates()] == ["one", "two"]
    assert store.checkpoint("a@gmail.com") == ("initial-10", None, None)
    store.close()


def test_history_pages_resume_without_repeat_or_stalling(tmp_path):
    store = gi.IntakeStore(tmp_path / "state.sqlite3")
    store.set_checkpoint("a@gmail.com", "base-1"); store.commit()
    class HistoryUsers(FakeUsers):
        def __init__(self):
            super().__init__([msg("one"), msg("two")], "new-2")
            self.calls = []
        def list(self, **kwargs):
            token = kwargs.get("pageToken")
            self.calls.append(token)
            if token is None:
                return FakeRequest({"historyId": "new-2", "history": [{"messagesAdded": [{"message": {"id": "one"}}]}], "nextPageToken": "h2"})
            return FakeRequest({"historyId": "new-2", "history": [{"messagesAdded": [{"message": {"id": "two"}}]}]})
    service = FakeService([]); service.u = HistoryUsers()
    first = gi.sync_account(store, "a@gmail.com", service, commit=True, maximum=1)
    assert first.remaining
    assert store.checkpoint("a@gmail.com") == ("base-1", "h2", "history")
    second = gi.sync_account(store, "a@gmail.com", service, commit=True, maximum=1)
    assert not second.remaining
    assert service.u.calls == [None, "h2"]
    assert store.checkpoint("a@gmail.com") == ("new-2", None, None)
    assert {row["message_id"] for row in store.candidates()} == {"one", "two"}
    store.close()


def test_action_states_and_shell_quoting(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    cmd = gi.action_for_company(cfg, "O'Reilly Inc")['commands'][0]
    assert cmd.startswith(".venv/bin/python -m job_research add-company ")
    assert "O'Reilly Inc" in __import__("shlex").split(cmd)[-1]
    research = gi.action_for_company(cfg, "O'Reilly Inc")["commands"][1]
    assert research.endswith("research-company 'O'\"'\"'Reilly Inc' --include-jobs")
    (cfg.companies_dir / "Acme.md").write_text("", encoding="utf-8")
    assert gi.action_for_company(cfg, "Acme")["action"] == "research"
    (cfg.references_dir / "Acme - Company Research.md").write_text("", encoding="utf-8")
    assert gi.action_for_company(cfg, "Acme")["action"] == "no-action"
    class NotePath:
        def __init__(self, name): self.name = name; self.stem = name[:-3]
        suffix = ".md"
        def is_file(self): return True
    monkeypatch.setattr(Path, "iterdir", lambda _self: iter([NotePath("Acme.md"), NotePath("ACME.md")]))
    assert gi.action_for_company(cfg, "acme")["action"] == "ambiguous-note"


def test_gmail_intake_all_keeps_account_failure_independent(tmp_path, monkeypatch, capsys):
    from job_research import cli
    cfg = config(tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda _: cfg)
    monkeypatch.setattr(gi, "state_path", lambda: tmp_path / "state.sqlite3")
    service = FakeService([msg("work-message")])
    def gmail_service(account):
        if account == gi.ACCOUNTS["personal"]:
            raise RuntimeError("personal auth unavailable")
        return service, account
    monkeypatch.setattr(gi, "gmail_service", gmail_service)
    assert cli.main(["gmail-intake", "all", "--commit"]) == 0
    result = __import__("json").loads(capsys.readouterr().out)
    assert result["accounts"][0]["status"] == "failed"
    assert result["accounts"][1]["email"] == gi.ACCOUNTS["work"]
    store = gi.IntakeStore(tmp_path / "state.sqlite3")
    assert store.checkpoint(gi.ACCOUNTS["work"])[0] == "10"
    assert store.checkpoint(gi.ACCOUNTS["personal"]) == (None, None, None)
    store.close()


def test_existing_add_company_note_is_unchanged(tmp_path):
    from job_research.cli import add_company
    cfg = config(tmp_path)
    template = tmp_path / "Templates" / "Company.md"; template.parent.mkdir(); template.write_text("template", encoding="utf-8")
    existing = cfg.companies_dir / "Exact.md"; existing.write_text("personal edits", encoding="utf-8")
    before = existing.read_bytes()
    assert add_company(cfg, "Exact")["status"] == "already-exists"
    assert existing.read_bytes() == before

