from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Config:
    project_dir: Path
    vault_path: Path
    companies_dir: Path
    references_dir: Path
    discovery: dict[str, Any]
    updates: dict[str, Any]
    job_filters: dict[str, list[str]]


@dataclass
class CompanyNote:
    name: str
    path: Path
    frontmatter: dict[str, Any]
    body: str

    @property
    def careers_url(self) -> str:
        return str(self.frontmatter.get("careers-url") or "").strip()

    @property
    def primary_job_url(self) -> str:
        return str(self.frontmatter.get("primary-job-posting-url") or "").strip()


@dataclass
class Page:
    url: str
    title: str
    markdown: str
    text: str
    rendered: bool = False
    category: str = "Company overview"
    error: str | None = None


@dataclass
class Job:
    provider: str
    job_id: str
    title: str
    url: str
    location: str = ""
    arrangement: str = ""
    description_html: str = ""
    description_markdown: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

