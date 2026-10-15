from job_research.jobs import board_identity, fetch_generic_jobs, fill_description, matches
from job_research.web import FetchResult
from job_research.model import Job


def test_board_identity():
    assert board_identity("https://job-boards.greenhouse.io/acme/jobs/123") == ("greenhouse", "acme")
    assert board_identity("https://jobs.lever.co/acme/123") == ("lever", "acme")
    assert board_identity("https://jobs.ashbyhq.com/Acme/123") == ("ashby", "Acme")


def test_filter_is_structured_and_configurable():
    filters = {
        "title_include": ["software engineer", "platform engineer"],
        "title_exclude": ["manager", "intern"],
        "locations": ["new york", "remote"],
    }
    assert matches(Job("x", "1", "Senior Software Engineer", "u", "New York, NY"), filters)
    assert not matches(Job("x", "2", "Engineering Manager", "u", "New York, NY"), filters)
    assert not matches(Job("x", "3", "Software Engineer", "u", "London"), filters)


class PostingClient:
    def get(self, url, *, allow_playwright=False):
        return FetchResult(url, """<html><head><title>Job | Acme</title></head><body><main>
        <h1>Senior Backend Engineer</h1><p>Build reliable payment systems.</p>
        </main></body></html>""", 200)


def test_generic_posting_uses_real_heading_before_filtering():
    job = Job("generic-html", "1", "Learn more", "https://acme.test/jobs/backend")
    filled = fill_description(job, PostingClient())
    assert filled.title == "Senior Backend Engineer"
    assert matches(filled, {"title_include": ["backend engineer"]})


class NextIndexClient:
    def get(self, url, *, allow_playwright=False):
        return FetchResult(url, """<html><body>
        <a href="/careers/listing/product-manager/100">Product Manager</a>
        <script id="__NEXT_DATA__" type="application/json">
        {"props":{"jobs":[
          {"greenhouseId":100,"title":"Product Manager","slug":"product-manager"},
          {"greenhouseId":200,"title":"Senior Software Engineer","slug":"senior-software-engineer"}
        ]}}
        </script></body></html>""", 200)


def test_generic_jobs_expand_complete_next_index_from_observed_listing_shape():
    jobs = fetch_generic_jobs(["https://acme.test/careers/search"], NextIndexClient())
    assert [(job.job_id, job.title, job.url) for job in jobs] == [
        ("200", "Senior Software Engineer", "https://acme.test/careers/listing/senior-software-engineer/200")
    ]

