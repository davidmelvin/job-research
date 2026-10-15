from __future__ import annotations

from pathlib import Path
import yaml

from .model import Config


def load_config(path: str | Path) -> Config:
    path = Path(path).expanduser().resolve()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    project_dir = path.parent
    configured_vault = Path(raw.get("vault_path", "../.." )).expanduser()
    vault = (configured_vault if configured_vault.is_absolute() else project_dir / configured_vault).resolve()
    companies = vault / raw.get("company_notes_directory", "Companies")
    references = vault / raw.get("references_directory", "References")
    if not companies.is_dir():
        raise ValueError(f"Company notes directory does not exist: {companies}")
    if not references.is_dir():
        raise ValueError(f"References directory does not exist: {references}")
    return Config(
        project_dir=project_dir,
        vault_path=vault,
        companies_dir=companies,
        references_dir=references,
        discovery=raw.get("discovery", {}),
        updates=raw.get("updates", {}),
        job_filters=raw.get("job_filters", {}),
    )

