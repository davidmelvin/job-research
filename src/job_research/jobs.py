from __future__ import annotations

import re
import hashlib
import json
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup
from markdownify import markdownify

from .model import Job
from .web import WebClient, careers_hub_score, extract_links, same_organization


def board_identity(url: str) -> tuple[str, str] | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if "greenhouse.io" in host:
        if parts and parts[0] in {"jobs", "embed"} and len(parts) > 1:
            return "greenhouse", parts[1]
        if parts:
            return "greenhouse", parts[0]
    if host.endswith("lever.co") and parts:
        return "lever", parts[0]
    if "ashbyhq.com" in host and parts:
        return "ashby", parts[0]
    return None


def discover_board(urls: list[str], client: WebClient) -> tuple[str, str] | None:
    for url in urls:
        identity = board_identity(url)
        if identity:
            return identity
    for url in urls:
        try:
            page = client.get(url)
            for href, label in extract_links(page.html, page.url):
                if any(x in f"{href} {label}".casefold() for x in ("job", "career", "opening")):
                    identity = board_identity(href)
                    if identity:
                        return identity
        except Exception:
            continue
    return None


def fetch_jobs(provider: str, board: str, client: WebClient) -> list[Job]:
    if provider == "greenhouse":
        data = client.get_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true")
        return [Job(
            provider, str(item["id"]), item.get("title", "Untitled"), item.get("absolute_url", ""),
            (item.get("location") or {}).get("name", ""),
            description_html=item.get("content", ""), metadata=item,
        ) for item in data.get("jobs", [])]
    if provider == "lever":
        data = client.get_json(f"https://api.lever.co/v0/postings/{board}?mode=json")
        return [Job(
            provider, str(item["id"]), item.get("text", "Untitled"), item.get("hostedUrl", ""),
            (item.get("categories") or {}).get("location", ""),
            (item.get("categories") or {}).get("commitment", ""),
            item.get("description", "") + "\n" + item.get("additional", ""), metadata=item,
        ) for item in data]
    if provider == "ashby":
        data = client.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}")
        return [Job(
            provider, str(item.get("id") or item.get("jobUrl", "").rstrip("/").split("/")[-1]),
            item.get("title", "Untitled"), item.get("jobUrl", ""), item.get("location", ""),
            item.get("workplaceType", ""), item.get("descriptionHtml", ""), metadata=item,
        ) for item in data.get("jobs", [])]
    raise ValueError(f"Unsupported ATS provider: {provider}")


def fetch_generic_jobs(urls: list[str], client: WebClient) -> list[Job]:
    """Best-effort fallback for official career pages without a supported public API."""
    jobs: list[Job] = []
    seen: set[str] = set()
    title_terms = ("engineer", "developer", "software", "backend", "frontend", "full stack", "platform", "infrastructure")
    sources = list(dict.fromkeys(urls))
    source_index = 0
    while source_index < len(sources) and source_index < 12:
        source = sources[source_index]
        source_index += 1
        try:
            result = client.get(source, allow_playwright=True)
        except Exception:
            continue
        page_links = extract_links(result.html, result.url)
        for href, label in page_links:
            if same_organization(result.url, href) and careers_hub_score(href, label) >= 20 and href not in sources:
                sources.append(href)
        listing_templates: list[tuple[str, str]] = []
        for href, _label in page_links:
            match = re.match(r"(.*/(?:careers|jobs)/listing/)([^/]+)/(\d+)(?:/.*)?$", href)
            if match:
                listing_templates.append((match.group(1), href))
        for href, label in page_links:
            label = re.sub(r"\s+", " ", label).strip()
            haystack = f"{href} {label}".casefold()
            if href in seen or not any(term in haystack for term in title_terms):
                continue
            if not any(term in haystack for term in ("job", "career", "position", "opening", "role")):
                continue
            seen.add(href)
            stable_id = hashlib.sha256(href.encode("utf-8")).hexdigest()[:16]
            jobs.append(Job("generic-html", stable_id, label or "Software engineering role", href, metadata={"title_confident": bool(label and any(term in label.casefold() for term in title_terms))}))

        # Some server-rendered careers sites show one page of links but place the
        # complete job index in Next.js data. Reuse the observed official listing
        # URL shape instead of inventing a provider-specific endpoint.
        if listing_templates:
            soup = BeautifulSoup(result.html, "html.parser")
            script = soup.select_one("script#__NEXT_DATA__[type='application/json']")
            try:
                next_data = json.loads(script.string or script.get_text()) if script else None
            except (TypeError, json.JSONDecodeError):
                next_data = None

            def records(value):
                if isinstance(value, dict):
                    if value.get("title") and value.get("slug") and (value.get("greenhouseId") or value.get("id")):
                        yield value
                    for child in value.values():
                        yield from records(child)
                elif isinstance(value, list):
                    for child in value:
                        yield from records(child)

            prefix, _example = listing_templates[0]
            for item in records(next_data):
                if not any(term in str(item["title"]).casefold() for term in title_terms):
                    continue
                external_id = str(item.get("greenhouseId") or item.get("id"))
                href = f"{prefix}{item['slug']}/{external_id}"
                if href in seen:
                    continue
                seen.add(href)
                jobs.append(Job(
                    "generic-html", external_id, str(item["title"]), href,
                    metadata={"title_confident": True, "index_record": item},
                ))
    return jobs


def title_matches(job: Job, filters: dict[str, list[str]]) -> bool:
    title = job.title.casefold()
    includes = [x.casefold() for x in filters.get("title_include", [])]
    excludes = [x.casefold() for x in filters.get("title_exclude", [])]
    return (not includes or any(x in title for x in includes)) and not any(x in title for x in excludes)


def matches(job: Job, filters: dict[str, list[str]]) -> bool:
    title = job.title.casefold()
    searchable = f"{job.title} {job.location} {job.arrangement} {BeautifulSoup(job.description_html, 'html.parser').get_text(' ')}".casefold()
    if not title_matches(job, filters):
        return False
    for key, target in (("specializations", searchable), ("seniority_include", title), ("locations", job.location.casefold()), ("arrangements", job.arrangement.casefold()), ("keywords", searchable)):
        requested = [x.casefold() for x in filters.get(key, [])]
        if requested and not any(x in target for x in requested):
            return False
    return True


def fill_description(job: Job, client: WebClient) -> Job:
    if job.description_html:
        job.description_markdown = markdownify(job.description_html, heading_style="ATX", bullets="-").strip()
        return job
    page = client.get(job.url, allow_playwright=True)
    soup = BeautifulSoup(page.html, "html.parser")
    posting: dict = {}
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except (TypeError, json.JSONDecodeError):
            continue
        values = data if isinstance(data, list) else [data]
        for value in values:
            if isinstance(value, dict) and value.get("@type") == "JobPosting":
                posting = value
                break
        if posting:
            break
    heading = soup.select_one("h1")
    page_title = soup.title.get_text(" ", strip=True) if soup.title else ""
    extracted_title = str(posting.get("title") or (heading.get_text(" ", strip=True) if heading else "") or page_title).strip()
    if extracted_title:
        job.title = extracted_title
    location = posting.get("jobLocation")
    if isinstance(location, list):
        location = location[0] if location else None
    if isinstance(location, dict):
        address = location.get("address", location)
        if isinstance(address, dict):
            job.location = ", ".join(str(address[key]).strip() for key in ("addressLocality", "addressRegion", "addressCountry") if address.get(key))
    if not job.arrangement and str(posting.get("jobLocationType", "")).casefold() == "telecommute":
        job.arrangement = "Remote"
    for node in soup.select("script, style, nav, footer, form, iframe, svg"):
        node.decompose()
    main = soup.select_one("main, article, [role=main]") or soup.body or soup
    job.description_html = str(main)
    job.description_markdown = markdownify(job.description_html, heading_style="ATX", bullets="-").strip()
    return job

