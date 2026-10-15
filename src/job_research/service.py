from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

import yaml

from .jobs import discover_board, fetch_generic_jobs, fetch_jobs, fill_description, matches, title_matches
from .model import CompanyNote, Config, Job, Page
from .notes import atomic_write, obsidian_link, safe_filename, update_generated_links
from .web import WebClient, content_hash, discover_research


def now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def yaml_document(data: dict) -> str:
    return "---\n" + yaml.safe_dump(data, sort_keys=False, allow_unicode=True).strip() + "\n---\n"


def research_path(config: Config, company: str) -> Path:
    return config.references_dir / f"{safe_filename(company)} - Company Research.md"


def research_page_paths(config: Config, company: str, pages: list[Page]) -> list[Path]:
    paths: list[Path] = []
    used: set[str] = set()
    for page in pages:
        parts = [unquote(part) for part in urlparse(page.url).path.split("/") if part]
        label = " - ".join(parts) if parts else "Homepage"
        base = safe_filename(f"{company} - Company Research - {label}")
        candidate = f"{base}.md"
        if candidate.casefold() in used:
            candidate = f"{base} - {content_hash(page.url)[:8]}.md"
        used.add(candidate.casefold())
        paths.append(config.references_dir / candidate)
    return paths


def render_research_page(company: CompanyNote, page: Page, captured: datetime) -> str:
    frontmatter = {
        "reference-type": "Archived company research source",
        "company": f"[[Obsidian Job Search Tracker/Companies/{company.name}]]",
        "source-url": page.url,
        "source-title": page.title,
        "category": page.category,
        "archived-date": captured.date().isoformat(),
        "last-successful-retrieval": captured.isoformat(),
        "content-sha256": content_hash(page.markdown),
        "retrieved-with": "Playwright" if page.rendered else "HTTP",
        "automation-owned": True,
    }
    return "\n".join([
        yaml_document(frontmatter), f"# {page.title}", "", f"**URL:** {page.url}", "",
        page.markdown, "",
    ])


def render_research(company: CompanyNote, pages: list[Page], page_paths: list[Path], failures: list[str], captured: datetime, vault_path: Path) -> str:
    sources = [
        {
            "url": page.url,
            "title": page.title,
            "file": obsidian_link(vault_path, path, page.title),
            "captured": captured.isoformat(),
            "content-sha256": content_hash(page.markdown),
        }
        for page, path in zip(pages, page_paths)
    ]
    frontmatter = {
        "reference-type": "Archived company research",
        "company": f"[[Obsidian Job Search Tracker/Companies/{company.name}]]",
        "source-urls": [page.url for page in pages],
        "archived-date": captured.date().isoformat(),
        "last-successful-retrieval": captured.isoformat(),
        "automation-owned": True,
    }
    lines = [yaml_document(frontmatter), f"# {company.name} — Company Research", "", "> Automatically collected from official company websites.", "> Each captured page is stored in a separate Markdown file.", f"> Last updated: {captured.isoformat()}", ""]
    categories = ["What the Company Does", "Mission & Values", "Culture & Work Environment", "Engineering & Team Dynamics", "Benefits & Work Arrangements"]
    for category in categories:
        category_pages = [page for page in pages if page.category == category]
        if not category_pages:
            continue
        lines.extend([f"## {category}", ""])
        for page in category_pages:
            page_index = pages.index(page)
            lines.append(f"- {obsidian_link(vault_path, page_paths[page_index], page.title)} — {page.url}")
        lines.append("")
    lines.extend(["## Capture manifest", "", "```yaml", yaml.safe_dump({"sources": sources, "failed-sources": failures}, sort_keys=False, allow_unicode=True).strip(), "```", ""])
    return "\n".join(lines)


def render_job(company: CompanyNote, job: Job, captured: datetime) -> str:
    metadata = {
        "reference-type": "Archived job description",
        "company": f"[[Obsidian Job Search Tracker/Companies/{company.name}]]",
        "title": job.title,
        "location": job.location,
        "source-url": job.url,
        "job-id": str(job.job_id),
        "provider": job.provider,
        "archived-date": captured.date().isoformat(),
        "last-seen": captured.date().isoformat(),
        "automation-owned": True,
    }
    return "\n".join([
        yaml_document(metadata), f"# {job.title} — {company.name}", "",
        f"**Original posting:** {job.url}", "", "## Job Description", "",
        job.description_markdown.strip(), "",
    ])


def existing_job_links(config: Config, company: CompanyNote) -> list[str]:
    links: list[str] = []
    company_target = f"[[Obsidian Job Search Tracker/Companies/{company.name}]]"
    for path in config.references_dir.glob(f"{safe_filename(company.name)} - Job *.md"):
        text = path.read_text(encoding="utf-8", errors="replace")[:2000]
        if company_target in text:
            links.append(obsidian_link(config.vault_path, path, path.stem.removeprefix(company.name + " - Job ")))
    return links


def research_company(config: Config, company: CompanyNote, *, include_jobs: bool = False, dry_run: bool = False) -> dict:
    client = WebClient(config.discovery)
    try:
        official, careers, pages, failures = discover_research(company.name, [company.careers_url, company.primary_job_url], client)
        path = research_path(config, company.name)
        captured = now()
        page_paths = research_page_paths(config, company.name, pages)
        page_contents = [render_research_page(company, page, captured) for page in pages]
        content = render_research(company, pages, page_paths, failures, captured, config.vault_path)
        if path.exists() and failures:
            # A partial refresh must not erase previously captured sources.
            action = "preserve-existing-due-to-failures"
        else:
            action = "unchanged" if path.exists() and path.read_text(encoding="utf-8") == content else ("update" if path.exists() else "create")
        if not dry_run and action != "preserve-existing-due-to-failures":
            for page_path, page_content in zip(page_paths, page_contents):
                atomic_write(page_path, page_content)
            atomic_write(path, content)
        jobs_result = None
        if include_jobs:
            jobs_result = find_jobs(config, company, client=client, dry_run=dry_run, extra_urls=[careers or "", official])
        links = existing_job_links(config, company)
        if jobs_result:
            links = jobs_result["links"]
        changed = update_generated_links(company, obsidian_link(config.vault_path, path, "Company research"), links, dry_run)
        return {"company": company.name, "official_website": official, "careers_page": careers, "pages": len(pages), "playwright_pages": sum(p.rendered for p in pages), "failures": failures, "research_file": str(path), "research_page_files": [str(page_path) for page_path in page_paths], "file_action": action, "note_links_changed": changed, "jobs": jobs_result, "dry_run": dry_run}
    finally:
        client.close()


def find_jobs(config: Config, company: CompanyNote, *, client: WebClient | None = None, dry_run: bool = False, extra_urls: list[str] | None = None) -> dict:
    owns_client = client is None
    client = client or WebClient(config.discovery)
    try:
        urls = [company.careers_url, company.primary_job_url] + (extra_urls or [])
        discovery_failures: list[str] = []
        if not company.careers_url and not extra_urls:
            try:
                official, careers, _pages, _research_failures = discover_research(company.name, [company.primary_job_url], client)
                urls.extend([careers or "", official])
            except Exception as exc:
                discovery_failures.append(f"Careers-page discovery failed: {exc}")
        urls = list(dict.fromkeys(url for url in urls if url))
        board = discover_board(urls, client)
        if board:
            provider, token = board
            all_jobs = fetch_jobs(provider, token, client)
        else:
            provider, token = "generic-html", "official-careers-pages"
            all_jobs = fetch_generic_jobs(urls, client)
            if not all_jobs:
                raise ValueError("No supported ATS or confidently identifiable job links were found. Availability remains unconfirmed.")
        verified: list[Job] = []
        job_failures: list[str] = []
        candidates_to_verify = [
            job for job in all_jobs
            if not job.metadata.get("title_confident") or title_matches(job, config.job_filters)
        ]
        for job in candidates_to_verify:
            try:
                verified.append(fill_description(job, client))
            except Exception as exc:
                job_failures.append(f"{job.url}: {exc}")
        selected = [job for job in verified if matches(job, config.job_filters)]
        links: list[str] = []
        actions: list[dict] = []
        captured = now()
        for job in selected:
            suffix = safe_filename(f"{job.title} ({provider}-{job.job_id})")
            path = config.references_dir / f"{safe_filename(company.name)} - Job {suffix}.md"
            content = render_job(company, job, captured)
            action = "unchanged" if path.exists() and path.read_text(encoding="utf-8") == content else ("update" if path.exists() else "create")
            if not dry_run:
                atomic_write(path, content)
            links.append(obsidian_link(config.vault_path, path, job.title))
            actions.append({"title": job.title, "job_id": job.job_id, "path": str(path), "action": action})
        research = research_path(config, company.name)
        research_link = obsidian_link(config.vault_path, research, "Company research") if research.exists() else None
        changed = update_generated_links(company, research_link, links, dry_run)
        return {"company": company.name, "provider": provider, "board": token, "found": len(all_jobs), "verified": len(verified), "matched": len(selected), "failures": discovery_failures + job_failures, "files": actions, "links": links, "note_links_changed": changed, "dry_run": dry_run}
    finally:
        if owns_client:
            client.close()

