from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify
import trafilatura

from .model import Page

RELEVANT = {
    "careers": 9, "culture": 9, "values": 9, "life": 7, "benefits": 8,
    "engineering": 9, "technology": 5, "team": 6, "workplace": 7,
    "mission": 7, "about": 5, "people": 5, "remote": 7,
}
ERROR_MARKERS = ("captcha", "access denied", "verify you are human", "cloudflare ray id", "enable javascript")
MEDIA_EXTENSIONS = re.compile(r"\.(?:avif|gif|jpe?g|png|svg|webp|mp4|m4v|mov|webm|ogv)(?:$|[?#])", re.IGNORECASE)
VIDEO_HOSTS = ("youtube.com", "youtu.be", "vimeo.com", "wistia.com", "loom.com")


@dataclass
class FetchResult:
    url: str
    html: str
    status: int
    rendered: bool = False


class WebClient:
    def __init__(self, settings: dict):
        self.settings = settings
        self.user_agent = settings.get("user_agent", "ObsidianJobResearch/0.1")
        self.client = httpx.Client(
            follow_redirects=True,
            timeout=float(settings.get("http_timeout_seconds", 20)),
            headers={"User-Agent": self.user_agent, "Accept": "text/html,application/json"},
        )
        self.last_request: dict[str, float] = {}
        self.robots: dict[str, RobotFileParser] = {}

    def close(self) -> None:
        self.client.close()

    def _allowed(self, url: str) -> bool:
        if not self.settings.get("obey_robots_txt", True):
            return True
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self.robots:
            parser = RobotFileParser(urljoin(origin, "/robots.txt"))
            try:
                response = self.client.get(parser.url)
                parser.parse(response.text.splitlines() if response.status_code < 400 else [])
            except httpx.HTTPError:
                parser.parse([])
            self.robots[origin] = parser
        return self.robots[origin].can_fetch(self.user_agent, url)

    def get(self, url: str, *, allow_playwright: bool = False) -> FetchResult:
        if not self._allowed(url):
            raise ValueError(f"Blocked by robots.txt: {url}")
        host = urlparse(url).netloc
        delay = float(self.settings.get("delay_seconds", 0.75))
        remaining = delay - (time.monotonic() - self.last_request.get(host, 0))
        if remaining > 0:
            time.sleep(remaining)
        response = self.client.get(url)
        self.last_request[host] = time.monotonic()
        response.raise_for_status()
        result = FetchResult(str(response.url), response.text, response.status_code)
        if allow_playwright and self.needs_rendering(response.text):
            return self.render(result.url)
        return result

    def get_json(self, url: str) -> object:
        response = self.client.get(url, headers={"Accept": "application/json"})
        response.raise_for_status()
        return response.json()

    def render(self, url: str) -> FetchResult:
        if not self.settings.get("playwright_fallback", True):
            raise ValueError("Page appears incomplete and Playwright fallback is disabled")
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as manager:
                browser = manager.chromium.launch(headless=True)
                page = browser.new_page(user_agent=self.user_agent)
                response = page.goto(url, wait_until="networkidle", timeout=int(float(self.settings.get("http_timeout_seconds", 20)) * 1000))
                for selector in ("button[aria-expanded='false']", "details:not([open]) summary"):
                    try:
                        page.locator(selector).all()[0:20]
                        for element in page.locator(selector).all()[0:20]:
                            element.click(timeout=500)
                    except Exception:
                        pass
                content = page.content()
                final_url = page.url
                status = response.status if response else 200
                browser.close()
                return FetchResult(final_url, content, status, True)
        except ImportError as exc:
            raise RuntimeError("Playwright is not installed. Run ./setup.sh") from exc

    def needs_rendering(self, html: str) -> bool:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)
        minimum = int(self.settings.get("minimum_content_characters", 400))
        return len(text) < minimum or any(marker in text.casefold() for marker in ERROR_MARKERS)


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/") or "/", "", parsed.query, ""))


def same_organization(left: str, right: str) -> bool:
    def root(url: str) -> str:
        host = urlparse(url).hostname or ""
        parts = host.removeprefix("www.").split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else host
    return root(left) == root(right)


def site_origin(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), "/", "", "", ""))


def _add_site_candidates(candidates: list[str], url: str) -> None:
    """Prefer a company's site root while retaining a deep URL as a discovery seed."""
    if not url.startswith(("http://", "https://")) or is_ats_or_aggregator(url):
        return
    candidates.extend((site_origin(url), url))


def candidate_start_urls(name: str, note_urls: list[str], client: WebClient) -> list[str]:
    candidates: list[str] = []
    for url in note_urls:
        _add_site_candidates(candidates, url)
    # Key-free search is only a fallback discovery aid. Each result must pass identity validation.
    search = f"https://html.duckduckgo.com/html/?q={quote_plus(name + ' official company')}"
    try:
        result = client.get(search)
        soup = BeautifulSoup(result.html, "html.parser")
        for link in soup.select("a.result__a")[:5]:
            href = link.get("href", "")
            if "uddg=" in href:
                href = parse_qs(urlparse(href).query).get("uddg", [href])[0]
            _add_site_candidates(candidates, href)
    except Exception:
        pass
    return list(dict.fromkeys(candidates))


def is_ats_or_aggregator(url: str) -> bool:
    host = (urlparse(url).hostname or "").casefold()
    return any(token in host for token in (
        "greenhouse.io", "lever.co", "ashbyhq.com", "jobs.gem.com", "workday", "builtin.com",
        "linkedin.com", "indeed.com", "glassdoor.com", "wellfound.com",
    ))


def validate_identity(name: str, url: str, html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    heading = " ".join(h.get_text(" ", strip=True) for h in soup.select("h1")[:2])
    compact_name = re.sub(r"[^a-z0-9]", "", name.casefold())
    compact_evidence = re.sub(r"[^a-z0-9]", "", f"{url} {title} {heading}".casefold())
    return len(compact_name) >= 3 and compact_name in compact_evidence


def extract_links(html: str, base_url: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    links: list[tuple[str, str]] = []
    for anchor in soup.find_all("a", href=True):
        href = urljoin(base_url, anchor["href"])
        if urlparse(href).scheme not in ("http", "https"):
            continue
        text = anchor.get_text(" ", strip=True)
        links.append((normalize_url(href), text))
    return list(dict.fromkeys(links))


def relevance(url: str, label: str) -> int:
    haystack = f"{url} {label}".casefold()
    return max((score for word, score in RELEVANT.items() if word in haystack), default=0)


def careers_hub_score(url: str, label: str) -> int:
    """Score careers indexes above individual postings and application pages."""
    parsed = urlparse(url)
    path = parsed.path.casefold().rstrip("/")
    text = f"{path} {label}".casefold()
    if not any(term in text for term in ("career", "jobs", "openings", "positions")):
        return 0
    score = 10
    if path in ("/careers", "/jobs", "/careers/jobs", "/jobs/search", "/careers/search"):
        score += 30
    if any(term in text for term in ("search", "all jobs", "open roles", "open positions", "view jobs")):
        score += 12
    if any(term in path for term in ("/apply", "/application", "/listing/", "/posting/")):
        score -= 40
    if re.search(r"/(?:jobs?|positions?|openings?)/[^/]*\d{4,}(?:/|$)", path):
        score -= 30
    score -= min(path.count("/"), 5)
    return score


def category_for(url: str, title: str) -> str:
    value = f"{url} {title}".casefold()
    if any(x in value for x in ("engineering", "technology", "developer", "tech-blog")):
        return "Engineering & Team Dynamics"
    if any(x in value for x in ("benefit", "remote", "workplace", "work arrangement")):
        return "Benefits & Work Arrangements"
    if any(x in value for x in ("culture", "life-at", "people", "employee")):
        return "Culture & Work Environment"
    if any(x in value for x in ("mission", "values", "principles")):
        return "Mission & Values"
    return "What the Company Does"


def extract_page(result: FetchResult, settings: dict) -> Page:
    soup = BeautifulSoup(result.html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else result.url
    for node in soup.select(
        "script, style, noscript, iframe, canvas, svg, img, picture, video, audio, "
        "source, track, object, embed, form, dialog"
    ):
        node.decompose()
    for node in soup.find_all("a", href=True):
        href = str(node.get("href") or "")
        host = (urlparse(urljoin(result.url, href)).hostname or "").casefold()
        if MEDIA_EXTENSIONS.search(href) or any(video_host in host for video_host in VIDEO_HOSTS):
            node.decompose()
    for node in soup.select("nav, [role=navigation], [aria-label*=cookie i], [class*=cookie i], [id*=cookie i]"):
        node.decompose()
    main = soup.select_one("main, article, [role=main]") or soup.body or soup
    inclusive = markdownify(str(main), heading_style="ATX", bullets="-").strip()
    focused = trafilatura.extract(str(soup), output_format="markdown", include_links=True, include_tables=True, favor_recall=True) or ""
    # Culture pages often lose values cards and testimonials in article extractors.
    markdown = inclusive if len(inclusive) >= len(focused) * 0.8 else focused
    markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()
    text = main.get_text(" ", strip=True)
    minimum = int(settings.get("minimum_content_characters", 400))
    error = None
    if len(text) < minimum:
        error = f"Suspiciously short content ({len(text)} characters)"
    if any(marker in text.casefold() for marker in ERROR_MARKERS):
        error = "CAPTCHA, access-denied, or JavaScript interstitial detected"
    return Page(result.url, title, markdown, text, result.rendered, category_for(result.url, title), error)


def discover_research(name: str, note_urls: list[str], client: WebClient) -> tuple[str, str | None, list[Page], list[str]]:
    failures: list[str] = []
    official_url = ""
    homepage_result: FetchResult | None = None
    for candidate in candidate_start_urls(name, note_urls, client):
        try:
            result = client.get(candidate, allow_playwright=True)
            if validate_identity(name, result.url, result.html):
                official_url, homepage_result = result.url, result
                break
            failures.append(f"Identity not confirmed: {candidate}")
        except Exception as exc:
            failures.append(f"{candidate}: {exc}")
    if not homepage_result:
        raise ValueError("Could not identify an official company website confidently. " + "; ".join(failures[:3]))

    origin = f"{urlparse(official_url).scheme}://{urlparse(official_url).netloc}"
    candidates: dict[str, tuple[int, int, str]] = {normalize_url(homepage_result.url): (10, 0, "Homepage")}
    fetched_html = {normalize_url(homepage_result.url): homepage_result}
    careers_candidates: dict[str, int] = {}
    frontier = [(homepage_result.url, homepage_result.html, 0)]
    max_depth = int(client.settings.get("max_link_depth", 2))
    while frontier:
        source_url, source_html, depth = frontier.pop(0)
        if depth >= max_depth:
            continue
        links = extract_links(source_html, source_url)
        # Record all visible careers hubs even when the research crawl only follows
        # the highest-ranked subset of links.
        for url, label in links:
            if same_organization(official_url, url):
                career_score = careers_hub_score(url, label)
                if career_score > 0:
                    careers_candidates[url] = max(careers_candidates.get(url, 0), career_score)
        ranked = sorted(links, key=lambda item: (relevance(*item), careers_hub_score(*item)), reverse=True)
        for url, label in ranked[:40]:
            score = relevance(url, label)
            if score <= 0:
                continue
            if same_organization(official_url, url):
                candidates.setdefault(url, (score, depth + 1, label))
                if depth + 1 < max_depth and url not in fetched_html:
                    try:
                        response = client.get(url)
                        fetched_html[url] = response
                        frontier.append((url, response.html, depth + 1))
                    except Exception as exc:
                        failures.append(f"{url}: {exc}")
            elif is_ats_or_aggregator(url) and any(x in f"{url} {label}".casefold() for x in ("career", "job", "opening")):
                careers_candidates[url] = max(careers_candidates.get(url, 0), careers_hub_score(url, label) + 5)

    pages: list[Page] = []
    max_pages = int(client.settings.get("max_research_pages", 8))
    for url, _meta in sorted(candidates.items(), key=lambda item: item[1][0], reverse=True)[:max_pages]:
        try:
            result = fetched_html.get(url) or client.get(url, allow_playwright=True)
            page = extract_page(result, client.settings)
            if page.error and not page.rendered and client.settings.get("playwright_fallback", True):
                page = extract_page(client.render(result.url), client.settings)
            if page.error:
                failures.append(f"{url}: {page.error}")
            else:
                pages.append(page)
        except Exception as exc:
            failures.append(f"{url}: {exc}")
    if not pages:
        raise ValueError("No valid research pages were extracted. " + "; ".join(failures[:3]))
    careers_url = max(careers_candidates, key=careers_candidates.get) if careers_candidates else None
    return official_url, careers_url, pages, failures


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

