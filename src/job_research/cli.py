from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys

from .config import load_config
from .notes import find_company, list_companies
from .service import find_jobs, research_company, research_path
from . import gmail_intake


def append_run_ledger(config, results: list[dict], *, command: str, options: dict, dry_run: bool) -> Path:
    path = config.automation_dir / "job-research-runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).astimezone().isoformat()
    with path.open("a", encoding="utf-8") as handle:
        for result in results:
            entry = {
                "timestamp": timestamp,
                "command": command,
                "options": options,
                "dry_run": dry_run,
                **result,
            }
            handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    return path


def failed_run_entries(config) -> list[dict]:
    path = config.automation_dir / "job-research-runs.jsonl"
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("status") == "failed" or (entry.get("jobs") or {}).get("status") == "failed":
            entries.append(entry)
    return entries


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="job-research", description="Local research for the existing Obsidian job tracker")
    result.add_argument("--config", default=str(Path(__file__).resolve().parents[2] / "config.yaml"))
    sub = result.add_subparsers(dest="command", required=True)
    one = sub.add_parser("research-company")
    one.add_argument("company")
    one.add_argument("--include-jobs", action="store_true")
    one.add_argument("--dry-run", action="store_true")
    add = sub.add_parser("add-company", help="Create a company note from the Obsidian company template")
    add.add_argument("company")
    jobs = sub.add_parser("find-jobs")
    jobs.add_argument("company")
    jobs.add_argument("--dry-run", action="store_true")
    many = sub.add_parser("research-all")
    many.add_argument("--min-rating", type=float)
    many.add_argument("--missing-only", action="store_true")
    many.add_argument("--include-jobs", action="store_true")
    many.add_argument("--dry-run", action="store_true")
    many.add_argument("--limit", type=int, default=10, help="Maximum companies to process in this run; repeat the command to continue")
    sub.add_parser("show-failures", help="Show failed company attempts from the Obsidian run log")
    sub.add_parser("verify-playwright")
    auth = sub.add_parser("gmail-auth", help="Authenticate and verify one read-only Gmail account")
    auth.add_argument("account", choices=tuple(gmail_intake.ACCOUNTS))
    sync = sub.add_parser("gmail-intake", help="Read-only recruiting email intake")
    sync.add_argument("account", choices=(*gmail_intake.ACCOUNTS, "all"))
    sync.add_argument("--commit", action="store_true", help="Save candidates and checkpoint; otherwise preview only")
    sync.add_argument("--max-messages", type=int, default=500)
    sync.add_argument("--format", choices=("json", "shell"), default="json")
    candidates = sub.add_parser("gmail-candidates", help="List or show saved Gmail intake candidates")
    candidates.add_argument("--key")
    candidates.add_argument("--format", choices=("json", "shell"), default="json")
    resolve = sub.add_parser("gmail-resolve", help="Set an employer name for an unresolved candidate")
    resolve.add_argument("key")
    resolve.add_argument("employer")
    return result


def emit(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def verify_playwright() -> dict:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as manager:
        browser = manager.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("<main><h1>Rendered by Playwright</h1><p id='ok'>ready</p></main>")
        ok = page.locator("#ok").inner_text() == "ready"
        version = browser.version
        browser.close()
    return {"playwright": "ok" if ok else "failed", "chromium": version}


def add_company(config, name: str) -> dict:
    """Create a company note without replacing any existing file."""
    filename = name.strip()
    if (
        not filename
        or filename in {".", ".."}
        or filename != name
        or any(ord(char) < 32 or ord(char) == 127 for char in filename)
        or any(char in filename for char in '/\\:*?"<>|')
        or filename.endswith((".", " "))
    ):
        return {"status": "conflict", "company": name, "reason": "unsafe-filename"}

    target = config.companies_dir / f"{filename}.md"
    matches = [
        path for path in config.companies_dir.iterdir()
        if path.is_file() and path.suffix.casefold() == ".md" and path.stem.casefold() == filename.casefold()
    ]
    if len(matches) > 1:
        return {"status": "conflict", "company": filename, "reason": "ambiguous-case-insensitive-match", "matches": [str(path) for path in sorted(matches)]}
    if matches:
        return {"status": "already-exists", "company": filename, "path": str(matches[0])}

    template = config.vault_path / "Templates" / "Company.md"
    if not template.is_file():
        return {"status": "conflict", "company": filename, "reason": "template-not-found", "template": str(template)}

    content = template.read_text(encoding="utf-8")
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    except FileExistsError:
        return {"status": "already-exists", "company": filename, "path": str(target)}
    except OSError as exc:
        return {"status": "conflict", "company": filename, "reason": "create-failed", "error": str(exc)}
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
    except OSError as exc:
        target.unlink(missing_ok=True)
        return {"status": "conflict", "company": filename, "reason": "write-failed", "error": str(exc)}
    return {"status": "created", "company": filename, "path": str(target), "template": str(template)}


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "verify-playwright":
            emit(verify_playwright())
            return 0
        config = load_config(args.config)
        if args.command == "add-company":
            result = add_company(config, args.company)
            emit(result)
            return 0 if result["status"] in {"created", "already-exists"} else 1
        elif args.command == "gmail-auth":
            service, actual = gmail_intake.gmail_service(gmail_intake.ACCOUNTS[args.account])
            emit({"account": args.account, "email": actual, "scope": "gmail.readonly", "read_only": True})
        elif args.command == "gmail-intake":
            if args.max_messages < 1:
                raise ValueError("--max-messages must be at least 1")
            store = gmail_intake.IntakeStore(gmail_intake.state_path(), read_only=not args.commit)
            try:
                requested = list(gmail_intake.ACCOUNTS.items()) if args.account == "all" else [(args.account, gmail_intake.ACCOUNTS[args.account])]
                summaries = []
                for alias, account in requested:
                    try:
                        service, actual = gmail_intake.gmail_service(account)
                        result = gmail_intake.sync_account(store, actual, service, commit=args.commit, maximum=args.max_messages)
                        summary = {"account": alias, "email": actual, "mode": result.mode, "processed": result.processed, "skipped_before_cutoff": result.skipped_before_cutoff, "remaining": result.remaining, "dry_run": not args.commit}
                        if args.commit:
                            summary["candidates"] = [row for row in gmail_intake.candidate_rows(config, store) if row["account"] == actual]
                        summaries.append(summary)
                        if args.commit:
                            gmail_intake.audit(config, "sync", summary)
                    except Exception as exc:
                        if args.account != "all":
                            raise
                        failure = {"account": alias, "email": account, "status": "failed", "error": str(exc)}
                        summaries.append(failure)
                        if args.commit:
                            gmail_intake.audit(config, "sync-failed", failure)
                if args.format == "shell":
                    for summary in summaries:
                        for row in summary.get("candidates", []):
                            for command in row["recommendation"]["commands"]:
                                print(command)
                        if summary.get("status") == "failed":
                            print(f"# {summary['account']} failed: {summary['error']}")
                        else:
                            print(f"# {summary['account']}: {summary['processed']} new messages; remaining={str(summary['remaining']).lower()}")
                else:
                    emit({"accounts": summaries} if args.account == "all" else summaries[0])
            finally:
                store.close()
        elif args.command == "gmail-candidates":
            store = gmail_intake.IntakeStore(gmail_intake.state_path())
            try:
                rows = gmail_intake.candidate_rows(config, store)
                if args.key:
                    rows = [row for row in rows if row["key"] == args.key]
                if args.format == "shell":
                    for row in rows:
                        for command in row["recommendation"]["commands"]:
                            print(command)
                else:
                    emit({"candidates": rows})
            finally:
                store.close()
        elif args.command == "gmail-resolve":
            store = gmail_intake.IntakeStore(gmail_intake.state_path())
            try:
                if not store.known(args.key):
                    raise ValueError(f"Unknown candidate key: {args.key}")
                store.resolve(args.key, args.employer)
                gmail_intake.audit(config, "resolve", {"key": args.key, "employer": args.employer})
                emit({"key": args.key, "employer": args.employer, "status": "resolved"})
            finally:
                store.close()
        elif args.command == "show-failures":
            emit({"run_log": str(config.automation_dir / "job-research-runs.jsonl"), "failures": failed_run_entries(config)})
        elif args.command == "research-company":
            company = find_company(config.companies_dir, args.company)
            progress = lambda message: print(f"[{company.name}] {message}", file=sys.stderr, flush=True)
            emit(research_company(config, company, include_jobs=args.include_jobs, dry_run=args.dry_run, progress=progress))
        elif args.command == "find-jobs":
            company = find_company(config.companies_dir, args.company)
            progress = lambda message: print(f"[{company.name}] {message}", file=sys.stderr, flush=True)
            emit(find_jobs(config, company, dry_run=args.dry_run, progress=progress))
        elif args.command == "research-all":
            candidates = []
            for company in list_companies(config.companies_dir):
                rating = company.frontmatter.get("rating")
                if args.min_rating is not None:
                    try:
                        if float(rating) < args.min_rating:
                            continue
                    except (TypeError, ValueError):
                        continue
                if args.missing_only and research_path(config, company.name).exists():
                    continue
                candidates.append(company)
            if args.limit < 1:
                raise ValueError("--limit must be at least 1")
            selected = candidates[:args.limit]
            results = []
            ledger_path = None
            for index, company in enumerate(selected, start=1):
                def progress(message: str, *, index: int = index, company_name: str = company.name) -> None:
                    print(f"[{index}/{len(selected)} {company_name}] {message}", file=sys.stderr, flush=True)

                print(f"[{index}/{len(selected)} {company.name}] Starting", file=sys.stderr, flush=True)
                try:
                    result = research_company(config, company, include_jobs=args.include_jobs, dry_run=args.dry_run, progress=progress)
                    results.append(result)
                    print(f"[{index}/{len(selected)} {company.name}] Finished", file=sys.stderr, flush=True)
                except Exception as exc:
                    print(f"[{index}/{len(selected)} {company.name}] Failed: {exc}", file=sys.stderr, flush=True)
                    results.append({"company": company.name, "status": "failed", "error": str(exc)})
                if not args.dry_run:
                    ledger_path = append_run_ledger(
                        config,
                        [results[-1]],
                        command="research-all",
                        options={"missing_only": args.missing_only, "min_rating": args.min_rating, "include_jobs": args.include_jobs, "limit": args.limit},
                        dry_run=args.dry_run,
                    )
                    if index == 1:
                        print(f"Saving run results to {ledger_path}", file=sys.stderr, flush=True)
            if ledger_path is not None:
                print(f"Saved run results to {ledger_path}", file=sys.stderr, flush=True)
            emit({"selected": len(candidates), "processed": len(results), "remaining": max(0, len(candidates) - len(results)), "run_log": str(ledger_path) if ledger_path else None, "results": results})
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

