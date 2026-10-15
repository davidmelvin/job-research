from job_research.web import FetchResult, candidate_start_urls, careers_hub_score, extract_page, is_ats_or_aggregator, relevance, validate_identity


HTML = """<!doctype html><html><head><title>Acme | Reliable widgets</title></head><body>
<nav>Home Careers</nav><main><h1>Acme</h1><h2>Our values</h2>
<p>We build useful widgets and support people who build them.</p>
<ul><li>Kindness</li><li>Careful engineering</li></ul>
<p>Our benefits include flexible work and learning support.</p></main></body></html>"""


def test_identity_and_extraction_preserve_values():
    assert validate_identity("Acme", "https://acme.test", HTML)
    page = extract_page(FetchResult("https://acme.test/values", HTML, 200), {"minimum_content_characters": 20})
    assert "Our values" in page.markdown
    assert "Kindness" in page.markdown
    assert page.category == "Mission & Values"


def test_extraction_omits_image_svg_and_video_references():
    html = """<html><head><title>Acme</title></head><body><main><h1>Acme</h1>
    <p>Useful company text that is long enough for this extraction test.</p>
    <img src='/team.jpg' alt='Team photo'><svg><title>Logo</title></svg>
    <video poster='/demo.jpg'><source src='/demo.mp4'></video>
    <a href='https://youtube.com/watch?v=123'>Watch our video</a>
    </main></body></html>"""
    page = extract_page(FetchResult("https://acme.test", html, 200), {"minimum_content_characters": 20})
    assert "Useful company text" in page.markdown
    assert "team.jpg" not in page.markdown
    assert "Logo" not in page.markdown
    assert "demo.mp4" not in page.markdown
    assert "youtube" not in page.markdown


def test_relevance_prioritizes_culture():
    assert relevance("https://acme.test/culture", "Life here") > relevance("https://acme.test/legal", "Terms")


def test_gem_is_not_accepted_as_an_official_company_site():
    assert is_ats_or_aggregator("https://jobs.gem.com/acme/123")


class NoSearchClient:
    def get(self, _url):
        raise RuntimeError("search unavailable")


def test_deep_job_seed_prefers_company_origin():
    urls = candidate_start_urls("Stripe", ["https://stripe.com/jobs/listing/backend-engineer/6042172/apply"], NoSearchClient())
    assert urls[:2] == ["https://stripe.com/", "https://stripe.com/jobs/listing/backend-engineer/6042172/apply"]


def test_careers_hub_outranks_individual_application():
    assert careers_hub_score("https://stripe.com/jobs/search", "Search jobs") > careers_hub_score(
        "https://stripe.com/jobs/listing/backend-engineer/6042172/apply", "Apply"
    )

