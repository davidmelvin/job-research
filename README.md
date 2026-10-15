# Obsidian Job Research

This local Python tool lives in `~/code/job-research` and adds source archives to the existing **Obsidian Job Search Tracker** in the PersonalVault. It does not replace the Base, template, company notes, or properties. It reads company names from the tracker's `Companies/*.md`, starts with the existing `careers-url` and `primary-job-posting-url` properties, writes archives to the tracker's flat `References/` folder, and maintains one bounded block at the end of each company note. Each scraped company page gets its own Markdown file; the main company-research note is an index and capture manifest.

The company-research and job-discovery commands are independent. The tool does not use AI, a paid API, an external database, cloud hosting, or an Obsidian plugin.

## Safety model

- The tool never edits `Companies.base`, `Templates/`, ratings, statuses, assessments, or existing prose.
- It updates only files that it names and marks with `automation-owned: true`.
- It updates only the text between `<!-- job-research:generated:start -->` and `<!-- job-research:generated:end -->` in a company note.
- Writes are atomic. A failed fetch does not delete an existing archive.
- If any source fails during a refresh, an existing research archive is preserved intact and the JSON result reports the failures.
- `--dry-run` performs discovery but does not write.
- `research-all` stops above 10 selected companies unless you explicitly raise `--limit`.
- Downloaded text is treated as data. The tool does not execute scripts found on webpages.
- The crawler respects `robots.txt`, uses a delay per domain, follows at most two link levels, and archives at most eight research pages by default.

## Install

From this directory:

```sh
chmod +x setup.sh
./setup.sh
```

The script locates Python 3.10 or newer, creates `.venv`, installs the Python package and test runner, downloads only Playwright Chromium, and runs a local headless-render check. It does not need administrator access. Set `JOB_RESEARCH_PYTHON=/path/to/python3` if the automatic Python search does not find your preferred interpreter.

To update later:

```sh
.venv/bin/python -m pip install --upgrade -e . pytest
.venv/bin/python -m playwright install chromium
.venv/bin/python -m job_research verify-playwright
```

## Use

Obsidian does not need to be open. Run commands from this folder. `config.yaml` points to the Obsidian tracker, so generated Markdown stays in the vault while the code and Python environment remain under `~/code`.

Preview company research:

```sh
.venv/bin/python -m job_research research-company Stripe --dry-run
```

Research one company:

```sh
.venv/bin/python -m job_research research-company Stripe
```

Find jobs without refreshing company research:

```sh
.venv/bin/python -m job_research find-jobs Stripe --dry-run
.venv/bin/python -m job_research find-jobs Stripe
```

Research and find jobs in one invocation:

```sh
.venv/bin/python -m job_research research-company Stripe --include-jobs
```

Research companies that have a numeric rating of at least 7:

```sh
.venv/bin/python -m job_research research-all --min-rating 7 --dry-run --limit 10
```

Research companies that do not yet have an automated company-research archive:

```sh
.venv/bin/python -m job_research research-all --missing-only --dry-run --limit 10
```

The command emits structured JSON with discovered URLs, pages extracted, Playwright use, failures, postings found, and files that would be created or updated.

## Discovery and extraction

The tool prefers an official non-ATS URL already present in the company note. If none exists, it uses a key-free DuckDuckGo HTML result as a lead. It accepts a site only when the company name also appears in the returned domain, title, or primary heading. Ambiguous identity stops the run.

From a verified company-owned URL, the tool identifies the company origin separately from deep job or application URLs. It scores links that appear relevant to company overview, mission, values, culture, employee experience, engineering, benefits, and work arrangements. Careers hubs are ranked above individual postings. HTTP retrieval comes first. If the response is too short or looks like a JavaScript interstitial, Chromium renders it. The extractor favors inclusive `main` or `article` conversion so that values cards, testimonials, and benefits are less likely to disappear. It removes image, SVG, audio, and video elements and direct media links before Markdown conversion. Each research archive includes source URLs, retrieval method, timestamps, and SHA-256 content hashes.

The ATS adapters use public endpoints for Greenhouse, Lever, and Ashby. For other official careers pages, a conservative HTML fallback follows careers hubs and links whose labels and URLs identify software-engineering jobs. It also reads complete Next.js job indexes when the site supplies one and verifies each title-matched posting before applying all filters. A standalone `find-jobs` run discovers a careers hub when the company note does not provide one. Job identity includes the provider's stable ID or a canonical-URL hash, so postings with the same title remain separate. The output distinguishes candidate links (`found`), successfully fetched postings (`verified`), and filter matches (`matched`). Inaccessible or inconclusive boards produce an explicit error; the tool does not claim that the company has no jobs.

## Configuration

Edit `config.yaml`. The default `vault_path` is the absolute path to the PersonalVault's `Obsidian Job Search Tracker`; it can also be set to a path relative to `config.yaml`. Useful settings include:

- vault, company-note, and reference paths;
- page limit, link depth, timeout, request delay, concurrency, and Playwright fallback;
- title inclusions and exclusions;
- specialization, seniority, location, work-arrangement, and keyword filters.

Each nonempty filter group is required. For example, if `locations` contains `New York` and `Remote`, a posting must match one of those values. Empty lists do not restrict results.

The default filter includes common software-engineering titles and excludes management, internships, new-graduate roles, Principal, and Distinguished titles. These are editable defaults, not claims about the user's qualifications.

## Files and links

Company research uses the existing flat reference convention:

```text
References/Stripe - Company Research.md
References/Stripe - Company Research - Homepage.md
References/Stripe - Company Research - jobs.md
```

The first file is an index. Each following file contains one captured page.

Jobs include the provider and stable job ID:

```text
References/Stripe - Job Backend Engineer (greenhouse-12345).md
```

The company note gets links only in the generated block. Repeated runs replace that block instead of duplicating links.

## Testing

Run the deterministic tests without accessing the network:

```sh
.venv/bin/python -m pytest
```

The tests cover frontmatter parsing, note preservation, dry-run behavior, idempotent markers, ATS URL recognition, configurable filters, inclusive values extraction, provenance, and stable job IDs.

For a real-world pilot, use `--dry-run` on one company first. Then inspect the discovered identity and source list before running without `--dry-run`. Do not start a full 527-company run during setup.

## Troubleshooting

**Playwright is missing or Chromium does not launch.** Run `.venv/bin/python -m playwright install chromium`, then `verify-playwright`. On a managed computer, browser installation can require network approval.

**A page is blocked.** The result reports `robots.txt`, HTTP, CAPTCHA, authentication, or short-content failures. The tool does not bypass those controls. Keep the existing archive and try the source manually later.

**The wrong company could match.** Add a verified official homepage or careers URL to the existing `careers-url` field, then retry. The tool will not save research when identity is ambiguous.

**No supported ATS is found.** Add the official Greenhouse, Lever, or Ashby board to `careers-url`. Generic JavaScript and HTML boards remain explicitly unconfirmed rather than being treated as empty.

**Culture content looks incomplete.** Review the archive's source list and compare the company values, culture, benefits, and engineering pages. Raise `max_research_pages` only when needed. The capture manifest records failed sources, and a temporary failure does not erase the last successful archive.

## Current limits

- Search-engine HTML access is best-effort and can be blocked. Existing official URLs are more reliable.
- Identity validation is conservative but cannot resolve every company with a common name.
- Generic ATS systems use conservative HTML discovery and do not have provider-specific adapters.
- The `concurrency` setting is reserved for bounded future parallel retrieval; this first version processes pages serially to keep rate behavior predictable.
- The tool archives official wording. It does not summarize, rate, or assess companies or roles.

