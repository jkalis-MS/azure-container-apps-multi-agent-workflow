"""Tests for sandbox_fetch module."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.sandbox_fetch import (
    generate_fetch_script,
    group_urls_by_domain,
    inject_hallucination,
)


class TestGroupUrlsByDomain:
    def test_groups_by_domain(self):
        urls = [
            "https://learn.microsoft.com/en-us/azure/aks/overview",
            "https://learn.microsoft.com/en-us/azure/aks/concepts",
            "https://github.com/Azure-Samples/aks-demo",
            "https://github.com/Azure/aks-engine",
            "https://techcommunity.microsoft.com/blog/aks-post",
        ]
        groups = group_urls_by_domain(urls)
        assert "learn.microsoft.com" in groups
        assert "github.com" in groups
        assert "techcommunity.microsoft.com" in groups
        assert len(groups["learn.microsoft.com"]) == 2
        assert len(groups["github.com"]) == 2
        assert len(groups["techcommunity.microsoft.com"]) == 1

    def test_empty_urls(self):
        assert group_urls_by_domain([]) == {}

    def test_single_url(self):
        groups = group_urls_by_domain(["https://learn.microsoft.com/en-us/azure"])
        assert len(groups) == 1
        assert "learn.microsoft.com" in groups


class TestGenerateFetchScript:
    def test_generates_valid_python(self):
        urls = ["https://example.com/page1", "https://example.com/page2"]
        script = generate_fetch_script(urls)
        assert "import requests" in script
        assert "from bs4 import BeautifulSoup" in script
        assert "https://example.com/page1" in script
        assert "https://example.com/page2" in script
        assert "print(json.dumps(results))" in script

    def test_urls_encoded_as_json(self):
        urls = ["https://example.com/path?q=test&x=1"]
        script = generate_fetch_script(urls)
        # Verify the URL is properly JSON-encoded in the script
        assert "https://example.com/path?q=test&x=1" in script

    def test_script_has_error_handling(self):
        script = generate_fetch_script(["https://example.com"])
        assert "except Exception" in script


class TestInjectHallucination:
    def test_injection_is_always_applied_when_called(self):
        """Injection is unconditional when function is invoked."""
        script = generate_fetch_script(["https://example.com"])
        modified, was_injected = inject_hallucination(script, "test topic")
        assert was_injected is True
        assert "bing.com" in modified
        assert "HALLUCINATION" in modified

    def test_injection_preserves_original_logic(self):
        """Injected script should still contain original fetch logic."""
        script = generate_fetch_script(["https://example.com"])
        modified, _ = inject_hallucination(script, "test topic")
        assert "results = [fetch_url(u) for u in urls]" in modified
        assert "https://example.com" in modified


class TestFetchWithSandboxes:
    @pytest.fixture
    def mock_env(self):
        env = {
            "AZURE_SUBSCRIPTION_ID": "test-sub-id",
            "ACA_SANDBOX_RESOURCE_GROUP": "test-rg",
            "ACA_SANDBOX_GROUP_NAME": "test-group",
            "ACA_SANDBOXGROUP_REGION": "eastus2",
        }
        with patch.dict("os.environ", env):
            yield

    @pytest.mark.asyncio
    async def test_raises_without_env_vars(self):
        from tools.sandbox_fetch import fetch_with_sandboxes

        with patch.dict("os.environ", {
            "AZURE_SUBSCRIPTION_ID": "",
            "ACA_SANDBOX_RESOURCE_GROUP": "",
            "ACA_SANDBOX_GROUP_NAME": "",
        }):
            # Mock the SDK imports inside the function
            mock_sdk = MagicMock()
            mock_sdk.SandboxGroupClient = MagicMock
            mock_sdk.endpoint_for_region = lambda r: f"https://{r}.example.com"
            mock_sdk.EgressPolicy = MagicMock
            mock_sdk.EgressHostRule = MagicMock

            with patch.dict("sys.modules", {
                "azure": MagicMock(),
                "azure.identity": MagicMock(),
                "azure.identity.aio": MagicMock(DefaultAzureCredential=MagicMock),
                "azure.containerapps": MagicMock(),
                "azure.containerapps.sandbox": MagicMock(),
                "azure.containerapps.sandbox.aio": mock_sdk,
            }):
                with pytest.raises(ValueError, match="Missing required env vars"):
                    await fetch_with_sandboxes(
                        urls=["https://example.com"],
                        topic="test",
                    )

    @pytest.mark.asyncio
    async def test_group_urls_integration(self, mock_env):
        """Test that URL grouping works correctly in the context of sandbox fetch."""
        from tools.sandbox_fetch import group_urls_by_domain
        groups = group_urls_by_domain(["https://learn.microsoft.com/test", "https://github.com/test"])
        assert len(groups) == 2
        assert "learn.microsoft.com" in groups
        assert "github.com" in groups
