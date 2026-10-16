from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
from email.utils import parseaddr
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import time
from typing import Any, Iterable

from .notes import list_companies

ACCOUNTS = {"personal": "davidmelvinjr@gmail.com", "work": "davidmelvinwork@gmail.com"}
CUTOFF_MS = int(datetime.fromisoformat("2026-10-05T00:00:00-04:00").timestamp() * 1000)
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def app_support_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "job-research" / "gmail"


def state_path() -> Path:
    return app_support_dir() / "intake.sqlite3"


def decode_part(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")
    except Exception:
        return ""


def mime_text(payload: dict[str, Any]) -> str:
    """Extract plain text from Gmail MIME JSON without following links or HTML resources."""
    chunks: list[str] = []
    mime = payload.get("mimeType", "")
    if mime == "text/plain" and (body := payload.get("body", {}).get("data")):
        chunks.append(decode_part(body))
    for part in payload.get("parts", []) or []:
        chunks.append(mime_text(part))
    if not chunks and mime == "text/html" and (body := payload.get("body", {}).get("data")):
        html = decode_part(body)
        html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
        chunks.append(re.sub(r"(?s)<[^>]*>", " ", html))
    return re.sub(r"\s+", " ", " ".join(chunks)).strip()


def header_map(payload: dict[str, Any]) -> dict[str, str]:
    return {str(h.get("name", "")).casefold(): str(h.get("value", "")) for h in payload.get("headers", [])}


def classify_message(subject: str, sender: str, body: str, headers: dict[str, str]) -> tuple[str, str | None]:
    text = f"{subject} {sender} {body[:12000]}".casefold()
    sender_email = parseaddr(sender)[1].casefold()
    if any(word in text for word in ("application received", "application submitted", "interview", "schedule your", "we'd like to speak", "your application", "candidate portal", "assessment")):
        return "application_interview", None
    if any(word in text for word in ("job alert", "jobs matching", "new jobs for you", "job recommendations", "saved search")) or "job-alert" in headers.get("precedence", "").casefold():
        return "job_alert", None
    recruiting = any(word in text for word in ("recruiter", "recruiting", "opportunity", "position", "role", "hiring", "engineer", "software", "job opening", "career opportunity"))
    if not recruiting:
        return "unrelated", None
    agency = any(word in text for word in ("staffing", "talent solutions", "recruitment agency", "recruiting agency", "on behalf of our client", "our client is looking"))
    employer = None
    # Prefer explicit hiring-company language; avoid treating an agency as employer.
    patterns = [r"(?:is hiring|are hiring|is looking|seeks)\s+(?:at\s+)?([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,3})",
                r"(?:at|with)\s+([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,3})(?=\s+(?:is hiring|are hiring|is looking|seeks))",
                r"(?:role|position|opening)\s+at\s+([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,3})"]
    for pattern in patterns:
        match = re.search(pattern, f"{subject} {body}")
        if match:
            employer = match.group(1).strip(" .,:;")
            break
    if agency and employer and employer.casefold() not in {"our client", "a client", "the client"}:
        return "direct_outreach", employer
    if agency:
        return "unresolved", None
    if sender_email:
        domain = sender_email.rsplit("@", 1)[-1]
        if domain not in {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "linkedin.com"}:
            # Domain is a clue only; do not promote it to an employer name.
            pass
    return ("direct_outreach", employer) if employer else ("unresolved", None)


def normalize_message(account: str, message: dict[str, Any]) -> dict[str, Any]:
    payload = message.get("payload", {})
    headers = header_map(payload)
    subject, sender = headers.get("subject", ""), headers.get("from", "")
    body = mime_text(payload)
    category, employer = classify_message(subject, sender, body, headers)
    return {
        "key": f"{account}:{message['id']}", "account": account, "message_id": message["id"],
        "internal_date": int(message.get("internalDate", 0)), "thread_id": message.get("threadId", ""),
        "subject": subject, "sender": sender, "snippet": str(message.get("snippet", ""))[:500],
        "classification": category, "employer": employer,
    }


class IntakeStore:
    def __init__(self, path: Path, *, read_only: bool = False):
        self.path = path
        if read_only and path.exists():
            self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        elif read_only:
            self.db = sqlite3.connect(":memory:")
            self.db.execute("CREATE TABLE messages (key TEXT PRIMARY KEY, account TEXT NOT NULL, message_id TEXT NOT NULL, internal_date INTEGER NOT NULL, thread_id TEXT, subject TEXT, sender TEXT, snippet TEXT, classification TEXT, employer TEXT, resolved_employer TEXT, processed_at INTEGER NOT NULL)")
            self.db.execute("CREATE TABLE checkpoints (account TEXT PRIMARY KEY, history_id TEXT, scan_page_token TEXT, scan_mode TEXT)")
            self.db.commit()
            return
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not read_only:
            os.chmod(path, 0o600)
        if not read_only:
            self.db = sqlite3.connect(path)
            os.chmod(path, 0o600)
            self.db.execute("CREATE TABLE IF NOT EXISTS messages (key TEXT PRIMARY KEY, account TEXT NOT NULL, message_id TEXT NOT NULL, internal_date INTEGER NOT NULL, thread_id TEXT, subject TEXT, sender TEXT, snippet TEXT, classification TEXT, employer TEXT, resolved_employer TEXT, processed_at INTEGER NOT NULL)")
            self.db.execute("CREATE TABLE IF NOT EXISTS checkpoints (account TEXT PRIMARY KEY, history_id TEXT, scan_page_token TEXT, scan_mode TEXT)")
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(checkpoints)")}
            if "scan_mode" not in columns:
                self.db.execute("ALTER TABLE checkpoints ADD COLUMN scan_mode TEXT")
            self.db.commit()

    def known(self, key: str) -> bool:
        return self.db.execute("SELECT 1 FROM messages WHERE key=?", (key,)).fetchone() is not None

    def checkpoint(self, account: str) -> tuple[str | None, str | None, str | None]:
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(checkpoints)")}
        mode_column = "scan_mode" if "scan_mode" in columns else "NULL"
        row = self.db.execute(f"SELECT history_id, scan_page_token, {mode_column} FROM checkpoints WHERE account=?", (account,)).fetchone()
        return tuple(row) if row else (None, None, None)

    def set_checkpoint(self, account: str, history_id: str | None, page_token: str | None = None, mode: str | None = None) -> None:
        self.db.execute("INSERT INTO checkpoints(account, history_id, scan_page_token, scan_mode) VALUES (?, ?, ?, ?) ON CONFLICT(account) DO UPDATE SET history_id=excluded.history_id, scan_page_token=excluded.scan_page_token, scan_mode=excluded.scan_mode", (account, history_id, page_token, mode))

    def insert(self, row: dict[str, Any]) -> None:
        self.db.execute("INSERT OR IGNORE INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)", (row["key"], row["account"], row["message_id"], row["internal_date"], row["thread_id"], row["subject"], row["sender"], row["snippet"], row["classification"], row["employer"], int(time.time())))

    def commit(self) -> None:
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def candidates(self) -> list[dict[str, Any]]:
        self.db.row_factory = sqlite3.Row
        return [dict(row) for row in self.db.execute("SELECT * FROM messages ORDER BY internal_date, key")]

    def resolve(self, key: str, employer: str) -> None:
        self.db.execute("UPDATE messages SET resolved_employer=? WHERE key=?", (employer, key))
        self.db.commit()


def _list_ids(service, query: str, page_token: str | None, maximum: int) -> tuple[list[dict], str | None]:
    response = service.users().messages().list(userId="me", q=query, maxResults=min(500, maximum), pageToken=page_token).execute()
    return response.get("messages", []), response.get("nextPageToken")


def fetch_message(service, message_id: str) -> dict:
    return service.users().messages().get(userId="me", id=message_id, format="full").execute()


def _expired(exc: Exception) -> bool:
    return getattr(getattr(exc, "resp", None), "status", None) == 404


@dataclass
class SyncResult:
    account: str
    mode: str
    processed: int
    skipped_before_cutoff: int
    remaining: bool
    history_id: str | None


def sync_account(store: IntakeStore, account: str, service, *, commit: bool, maximum: int = 500) -> SyncResult:
    old_history, old_token, saved_mode = store.checkpoint(account)
    mode = saved_mode or ("initial" if old_history is None else "history")
    ids: list[str] = []
    next_history: str | None = None
    remaining = False
    page_token: str | None = None
    if mode == "history":
        try:
            page_token = old_token
            while len(ids) < maximum:
                page = service.users().history().list(userId="me", startHistoryId=old_history, historyTypes=["messageAdded"], maxResults=maximum - len(ids), pageToken=page_token).execute()
                next_history = page.get("historyId", next_history)
                for item in page.get("history", []):
                    ids.extend(m["id"] for m in item.get("messagesAdded", []) for m in [m.get("message", m)])
                page_token = page.get("nextPageToken")
                if not page_token:
                    break
            remaining = bool(page_token)
        except Exception as exc:
            if not _expired(exc):
                raise
            mode = "recovery"
            old_token = None
            ids = []
    if mode in {"initial", "recovery"}:
        query = "after:2026/10/04"
        page_token = old_token if mode == "initial" else None
        next_history = old_history if mode == "initial" else None
        while len(ids) < maximum:
            batch, next_token = _list_ids(service, query, page_token, maximum - len(ids))
            ids.extend(item["id"] for item in batch)
            page_token = next_token
            if not page_token:
                break
        remaining = bool(page_token)
        if mode == "recovery" or next_history is None:
            meta = service.users().getProfile(userId="me").execute()
            next_history = meta.get("historyId")
    ids = list(dict.fromkeys(ids))[:maximum]
    processed = skipped = 0
    for message_id in ids:
        key = f"{account}:{message_id}"
        if store.known(key):
            continue
        row = normalize_message(account, fetch_message(service, message_id))
        if row["internal_date"] < CUTOFF_MS:
            skipped += 1
            continue
        if commit:
            store.insert(row)
        processed += 1
    if commit:
        # Keep the account on the same baseline until every page has been consumed.
        if remaining:
            store.set_checkpoint(account, next_history if mode == "initial" else old_history, page_token, mode)
        else:
            store.set_checkpoint(account, next_history, None, None)
        store.commit()
    return SyncResult(account, mode, processed, skipped, remaining, next_history)


def gmail_service(account: str):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    root = app_support_dir()
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    client = root / "oauth-client.json"
    token = root / f"token-{account}.json"
    creds = Credentials.from_authorized_user_file(token, SCOPES) if token.exists() else None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token.write_text(creds.to_json(), encoding="utf-8")
        os.chmod(token, 0o600)
    if not creds or not creds.valid:
        if not client.is_file():
            raise ValueError(f"OAuth desktop client file is missing: {client}")
        flow = InstalledAppFlow.from_client_secrets_file(client, SCOPES)
        creds = flow.run_local_server(port=0)
        token.write_text(creds.to_json(), encoding="utf-8")
        os.chmod(token, 0o600)
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    profile = service.users().getProfile(userId="me").execute()
    actual = profile.get("emailAddress", "").casefold()
    if actual != account.casefold():
        raise ValueError(f"Authenticated account mismatch: expected {account}, got {actual}")
    return service, actual


def research_archive_exists(config, name: str) -> bool:
    return (config.references_dir / f"{name} - Company Research.md").exists()


def action_for_company(config, employer: str | None) -> dict[str, Any]:
    if not employer:
        return {"action": "unresolved", "commands": []}
    matches = [p for p in config.companies_dir.iterdir() if p.is_file() and p.suffix.casefold() == ".md" and p.stem.casefold() == employer.casefold()]
    if len(matches) > 1:
        return {"action": "ambiguous-note", "matches": [p.stem for p in matches], "commands": []}
    if not matches:
        return {"action": "add-and-research", "commands": [f".venv/bin/python -m job_research add-company {shlex.quote(employer)}", f".venv/bin/python -m job_research research-company {shlex.quote(employer)} --include-jobs"]}
    name = matches[0].stem
    if not research_archive_exists(config, name):
        return {"action": "research", "commands": [f".venv/bin/python -m job_research research-company {shlex.quote(name)} --include-jobs"]}
    return {"action": "no-action", "commands": []}


def candidate_rows(config, store: IntakeStore) -> list[dict[str, Any]]:
    output = []
    for row in store.candidates():
        employer = row.get("resolved_employer") or row.get("employer")
        row["recommendation"] = action_for_company(config, employer)
        output.append(row)
    return output


def audit(config, event: str, data: dict[str, Any]) -> None:
    path = config.automation_dir / "gmail-company-intake.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"timestamp": datetime.now().astimezone().isoformat(), "event": event, **data}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

