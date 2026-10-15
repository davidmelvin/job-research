from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

from .model import CompanyNote

START = "<!-- job-research:generated:start -->"
END = "<!-- job-research:generated:end -->"


def read_company(path: Path) -> CompanyNote:
    text = path.read_text(encoding="utf-8")
    frontmatter: dict[str, Any] = {}
    body = text
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n?", text, re.DOTALL)
    if match:
        frontmatter = yaml.safe_load(match.group(1)) or {}
        body = text[match.end():]
    return CompanyNote(path.stem, path, frontmatter, body)


def find_company(companies_dir: Path, name: str) -> CompanyNote:
    exact = companies_dir / f"{name}.md"
    if exact.exists():
        return read_company(exact)
    matches = [p for p in companies_dir.glob("*.md") if p.stem.casefold() == name.casefold()]
    if len(matches) != 1:
        raise ValueError(f"Company note not found or ambiguous: {name}")
    return read_company(matches[0])


def list_companies(companies_dir: Path) -> list[CompanyNote]:
    return [read_company(p) for p in sorted(companies_dir.glob("*.md"), key=lambda x: x.name.casefold())]


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def generated_block(research_link: str | None, job_links: list[str]) -> str:
    lines = [START, "### Automated references", ""]
    if research_link:
        lines.append(f"- Company research: {research_link}")
    lines.extend(f"- Job posting: {link}" for link in sorted(set(job_links)))
    if not research_link and not job_links:
        lines.append("- No references collected.")
    lines.extend([END, ""])
    return "\n".join(lines)


def update_generated_links(note: CompanyNote, research_link: str | None, job_links: list[str], dry_run: bool) -> bool:
    original = note.path.read_text(encoding="utf-8")
    block = generated_block(research_link, job_links)
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END) + r"\n?", re.DOTALL)
    if pattern.search(original):
        updated = pattern.sub(block, original, count=1)
    else:
        updated = original.rstrip() + "\n\n" + block
    if updated == original:
        return False
    if not dry_run:
        atomic_write(note.path, updated)
    return True


def obsidian_link(vault: Path, path: Path, label: str | None = None) -> str:
    target = path.relative_to(vault).with_suffix("").as_posix()
    return f"[[{target}{'|' + label if label else ''}]]"


def safe_filename(value: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|]", "-", value).strip().rstrip(".")
    value = re.sub(r"\s+", " ", value)
    return value[:180] or "Untitled"

