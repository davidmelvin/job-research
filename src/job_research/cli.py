from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from .config import load_config
from .notes import find_company, list_companies
from .service import find_jobs, research_company, research_path


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


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "verify-playwright":
            emit(verify_playwright())
            return 0
        config = load_config(args.config)
        if args.command == "show-failures":
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

