from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .config import load_config
from .notes import find_company, list_companies
from .service import find_jobs, research_company, research_path


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
    many.add_argument("--limit", type=int, default=10, help="Safety limit; raise it explicitly for larger batches")
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
        if args.command == "research-company":
            emit(research_company(config, find_company(config.companies_dir, args.company), include_jobs=args.include_jobs, dry_run=args.dry_run))
        elif args.command == "find-jobs":
            emit(find_jobs(config, find_company(config.companies_dir, args.company), dry_run=args.dry_run))
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
            if len(candidates) > args.limit:
                raise ValueError(f"Batch contains {len(candidates)} companies, above the safety limit {args.limit}. Review the selection and pass a larger --limit explicitly.")
            results = []
            for company in candidates:
                try:
                    results.append(research_company(config, company, include_jobs=args.include_jobs, dry_run=args.dry_run))
                except Exception as exc:
                    results.append({"company": company.name, "status": "failed", "error": str(exc)})
            emit({"processed": len(results), "results": results})
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

