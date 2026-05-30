"""Tests for extract_links — depth-1 trusted link extraction from fetched pages."""

import pytest
from tools.extract_links import (
    extract_trusted_links,
    extract_links_from_fetched,
    _is_trusted,
    _normalise,
    TRUSTED_DOMAINS,
)


# ---------------------------------------------------------------------------
# Sample HTML fixtures
# ---------------------------------------------------------------------------

TECH_COMMUNITY_HTML = """
<html>
<head><title>Azure Container Apps + AI</title></head>
<body>
<nav><a href="/tag/azure">Azure</a></nav>
<main>
  <article>
    <h1>What's new in Azure Container Apps</h1>
    <p>Check the <a href="https://learn.microsoft.com/azure/container-apps/overview">official docs</a>
       for a full overview.</p>
    <p>We also published a <a href="https://github.com/Azure-Samples/container-apps-ai">sample repo</a>
       on GitHub.</p>
    <p>Read the <a href="https://azure.microsoft.com/products/container-apps/">product page</a>.</p>
    <p>See also <a href="https://techcommunity.microsoft.com/blog/related-post">related post</a>.</p>
    <p>External link: <a href="https://stackoverflow.com/questions/aca">SO question</a></p>
    <p>Anchor: <a href="#section2">jump to section</a></p>
    <p>PDF: <a href="https://learn.microsoft.com/whitepaper.pdf">whitepaper</a></p>
  </article>
</main>
<footer><a href="/login">Login</a></footer>
</body>
</html>
"""

LEARN_DOC_HTML = """
<html><body>
<main>
  <h1>Azure Container Apps overview</h1>
  <p>Azure Container Apps is a serverless platform.</p>
  <h2>See also</h2>
  <ul>
    <li><a href="https://learn.microsoft.com/azure/container-apps/quickstart">Quickstart</a></li>
    <li><a href="https://learn.microsoft.com/azure/container-apps/networking">Networking</a></li>
    <li><a href="https://github.com/Azure/azure-container-apps">Source code</a></li>
  </ul>
  <h2>Next steps</h2>
  <p><a href="https://learn.microsoft.com/azure/container-apps/tutorial">Tutorial</a></p>
</main>
</body></html>
"""

GITHUB_README_HTML = """
<html><body>
<article>
  <h1>azure-samples/container-apps-ai</h1>
  <p>Deploy AI workloads on ACA.</p>
  <p>Based on <a href="https://learn.microsoft.com/azure/container-apps/gpu">GPU docs</a>.</p>
  <p>See <a href="https://azure.microsoft.com/products/container-apps/">product page</a>.</p>
  <p>CI badge: <a href="https://github.com/Azure-Samples/container-apps-ai/actions">Actions</a></p>
  <p>Random: <a href="https://example.com/unrelated">unrelated</a></p>
</article>
</body></html>
"""

EMPTY_HTML = "<html><body></body></html>"

NO_LINKS_HTML = "<html><body><main><p>No links here.</p></main></body></html>"


# ---------------------------------------------------------------------------
# _is_trusted
# ---------------------------------------------------------------------------

class TestIsTrusted:
    @pytest.mark.parametrize("url", [
        "https://learn.microsoft.com/azure/aca",
        "https://github.com/Azure-Samples/repo",
        "https://azure.microsoft.com/products/aca/",
        "https://techcommunity.microsoft.com/blog/post",
        "https://devblogs.microsoft.com/azure/post",
        "https://code.visualstudio.com/docs",
        "https://developer.microsoft.com/graph",
    ])
    def test_trusted_domains_accepted(self, url):
        assert _is_trusted(url) is True

    @pytest.mark.parametrize("url", [
        "https://stackoverflow.com/q/123",
        "https://example.com/page",
        "https://evil-learn.microsoft.com.attacker.io/phish",
        "https://google.com/search?q=azure",
        "",
    ])
    def test_untrusted_domains_rejected(self, url):
        assert _is_trusted(url) is False


# ---------------------------------------------------------------------------
# _normalise
# ---------------------------------------------------------------------------

class TestNormalise:
    def test_strips_fragment(self):
        assert _normalise("https://learn.microsoft.com/page#section") == \
               "https://learn.microsoft.com/page"

    def test_strips_trailing_slash(self):
        assert _normalise("https://azure.microsoft.com/products/") == \
               "https://azure.microsoft.com/products"

    def test_preserves_path(self):
        assert _normalise("https://github.com/org/repo") == \
               "https://github.com/org/repo"


# ---------------------------------------------------------------------------
# extract_trusted_links — Tech Community / Blog (#1)
# ---------------------------------------------------------------------------

class TestExtractFromBlog:
    """Feature #1: Blog/TC link traversal."""

    def test_extracts_trusted_links(self):
        links = extract_trusted_links(TECH_COMMUNITY_HTML, "https://techcommunity.microsoft.com/blog/post")
        urls = [l["url"] for l in links]
        assert "https://learn.microsoft.com/azure/container-apps/overview" in urls
        assert "https://github.com/Azure-Samples/container-apps-ai" in urls
        assert "https://azure.microsoft.com/products/container-apps/" in urls
        assert "https://techcommunity.microsoft.com/blog/related-post" in urls

    def test_excludes_untrusted(self):
        links = extract_trusted_links(TECH_COMMUNITY_HTML, "https://techcommunity.microsoft.com/blog/post")
        urls = [l["url"] for l in links]
        assert not any("stackoverflow.com" in u for u in urls)

    def test_excludes_anchors(self):
        links = extract_trusted_links(TECH_COMMUNITY_HTML, "https://techcommunity.microsoft.com/blog/post")
        urls = [l["url"] for l in links]
        assert not any(u == "#section2" for u in urls)

    def test_excludes_pdfs(self):
        links = extract_trusted_links(TECH_COMMUNITY_HTML, "https://techcommunity.microsoft.com/blog/post")
        urls = [l["url"] for l in links]
        assert not any(u.endswith(".pdf") for u in urls)

    def test_excludes_nav_footer_links(self):
        links = extract_trusted_links(TECH_COMMUNITY_HTML, "https://techcommunity.microsoft.com/blog/post")
        urls = [l["url"] for l in links]
        # /tag/ from nav should be excluded
        assert not any("/tag/" in u for u in urls)
        # /login from footer should be excluded
        assert not any("/login" in u for u in urls)

    def test_link_text_captured(self):
        links = extract_trusted_links(TECH_COMMUNITY_HTML, "https://techcommunity.microsoft.com/blog/post")
        learn_link = next((l for l in links if "overview" in l["url"]), None)
        assert learn_link is not None
        assert learn_link["text"] == "official docs"

    def test_no_duplicates(self):
        html = """<html><body><main>
        <a href="https://learn.microsoft.com/page">Link 1</a>
        <a href="https://learn.microsoft.com/page">Link 2</a>
        <a href="https://learn.microsoft.com/page#fragment">Link 3</a>
        </main></body></html>"""
        links = extract_trusted_links(html, "https://example.com")
        urls = [l["url"] for l in links]
        # All three should deduplicate to one
        assert len([u for u in urls if "learn.microsoft.com/page" in u]) == 1


# ---------------------------------------------------------------------------
# extract_trusted_links — Learn "See Also" / "Next Steps" (#2)
# ---------------------------------------------------------------------------

class TestExtractFromLearn:
    """Feature #2: Learn doc related content extraction."""

    def test_extracts_see_also_links(self):
        links = extract_trusted_links(LEARN_DOC_HTML, "https://learn.microsoft.com/azure/container-apps/overview")
        urls = [l["url"] for l in links]
        assert "https://learn.microsoft.com/azure/container-apps/quickstart" in urls
        assert "https://learn.microsoft.com/azure/container-apps/networking" in urls

    def test_extracts_next_steps_links(self):
        links = extract_trusted_links(LEARN_DOC_HTML, "https://learn.microsoft.com/azure/container-apps/overview")
        urls = [l["url"] for l in links]
        assert "https://learn.microsoft.com/azure/container-apps/tutorial" in urls

    def test_extracts_github_from_learn(self):
        links = extract_trusted_links(LEARN_DOC_HTML, "https://learn.microsoft.com/azure/container-apps/overview")
        urls = [l["url"] for l in links]
        assert "https://github.com/Azure/azure-container-apps" in urls

    def test_excludes_self_link(self):
        links = extract_trusted_links(LEARN_DOC_HTML, "https://learn.microsoft.com/azure/container-apps/overview")
        urls = [l["url"] for l in links]
        assert "https://learn.microsoft.com/azure/container-apps/overview" not in urls


# ---------------------------------------------------------------------------
# extract_trusted_links — GitHub README (#3)
# ---------------------------------------------------------------------------

class TestExtractFromGitHub:
    """Feature #3: GitHub README link extraction."""

    def test_extracts_learn_from_readme(self):
        links = extract_trusted_links(GITHUB_README_HTML, "https://github.com/Azure-Samples/container-apps-ai")
        urls = [l["url"] for l in links]
        assert "https://learn.microsoft.com/azure/container-apps/gpu" in urls

    def test_extracts_product_page_from_readme(self):
        links = extract_trusted_links(GITHUB_README_HTML, "https://github.com/Azure-Samples/container-apps-ai")
        urls = [l["url"] for l in links]
        assert "https://azure.microsoft.com/products/container-apps/" in urls

    def test_excludes_untrusted_from_readme(self):
        links = extract_trusted_links(GITHUB_README_HTML, "https://github.com/Azure-Samples/container-apps-ai")
        urls = [l["url"] for l in links]
        assert not any("example.com" in u for u in urls)

    def test_includes_github_actions_link(self):
        """GitHub Actions URL is on github.com, so it's trusted."""
        links = extract_trusted_links(GITHUB_README_HTML, "https://github.com/Azure-Samples/container-apps-ai")
        urls = [l["url"] for l in links]
        assert "https://github.com/Azure-Samples/container-apps-ai/actions" in urls


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestExtractEdgeCases:
    def test_empty_html(self):
        links = extract_trusted_links(EMPTY_HTML, "https://example.com")
        assert links == []

    def test_no_links_html(self):
        links = extract_trusted_links(NO_LINKS_HTML, "https://example.com")
        assert links == []

    def test_relative_urls_resolved(self):
        html = """<html><body><main>
        <a href="/azure/aks/overview">AKS docs</a>
        </main></body></html>"""
        links = extract_trusted_links(html, "https://learn.microsoft.com/page")
        urls = [l["url"] for l in links]
        assert "https://learn.microsoft.com/azure/aks/overview" in urls


# ---------------------------------------------------------------------------
# extract_links_from_fetched (batch processing)
# ---------------------------------------------------------------------------

class TestExtractLinksFromFetched:
    def test_deduplicates_against_existing(self):
        fetched = [
            {"url": "https://techcommunity.microsoft.com/blog/post", "html": TECH_COMMUNITY_HTML},
        ]
        existing = {"https://learn.microsoft.com/azure/container-apps/overview"}
        results = extract_links_from_fetched(fetched, existing)
        urls = [r["url"] for r in results]
        # The existing URL should be excluded
        assert "https://learn.microsoft.com/azure/container-apps/overview" not in urls
        # But other trusted links should appear
        assert any("github.com" in u for u in urls)

    def test_includes_found_on(self):
        fetched = [
            {"url": "https://techcommunity.microsoft.com/blog/post", "html": TECH_COMMUNITY_HTML},
        ]
        results = extract_links_from_fetched(fetched, set())
        for r in results:
            assert "found_on" in r

    def test_skips_pages_without_html(self):
        fetched = [
            {"url": "https://example.com/page", "content": "text only, no html"},
        ]
        results = extract_links_from_fetched(fetched, set())
        assert results == []

    def test_popularity_sorting(self):
        """Links found on multiple pages should rank higher."""
        html_a = """<html><body><main>
        <a href="https://learn.microsoft.com/azure/popular">Popular</a>
        <a href="https://learn.microsoft.com/azure/only-a">Only A</a>
        </main></body></html>"""
        html_b = """<html><body><main>
        <a href="https://learn.microsoft.com/azure/popular">Popular</a>
        <a href="https://learn.microsoft.com/azure/only-b">Only B</a>
        </main></body></html>"""
        fetched = [
            {"url": "https://blog1.com", "html": html_a},
            {"url": "https://blog2.com", "html": html_b},
        ]
        results = extract_links_from_fetched(fetched, set())
        # "popular" should be first because it appears on 2 pages
        assert results[0]["url"] == "https://learn.microsoft.com/azure/popular"

    def test_caps_at_30_results(self):
        # Generate HTML with 40 unique trusted links
        links_html = "".join(
            f'<a href="https://learn.microsoft.com/page/{i}">Link {i}</a>\n'
            for i in range(40)
        )
        html = f"<html><body><main>{links_html}</main></body></html>"
        fetched = [{"url": "https://blog.com/post", "html": html}]
        results = extract_links_from_fetched(fetched, set())
        assert len(results) <= 30

    def test_multiple_pages_combined(self):
        fetched = [
            {"url": "https://techcommunity.microsoft.com/blog/post", "html": TECH_COMMUNITY_HTML},
            {"url": "https://learn.microsoft.com/azure/container-apps/overview", "html": LEARN_DOC_HTML},
            {"url": "https://github.com/Azure-Samples/container-apps-ai", "html": GITHUB_README_HTML},
        ]
        results = extract_links_from_fetched(fetched, set())
        urls = [r["url"] for r in results]
        # Should find links from all three source types
        assert any("learn.microsoft.com" in u for u in urls)
        assert any("github.com" in u for u in urls)
        assert any("azure.microsoft.com" in u for u in urls)
