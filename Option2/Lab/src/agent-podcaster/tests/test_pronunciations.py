"""Tests for the pronunciation replacement utility."""

import pytest
from tools.pronunciations import apply_pronunciations


class TestKnownTermsReplaced:
    """Known dictionary terms should be replaced with TTS-friendly versions."""

    def test_known_terms_replaced(self):
        result = apply_pronunciations("Deploy to AKS using kubectl")
        assert "A-K-S" in result
        assert "kube-control" in result
        assert "AKS" not in result
        assert "kubectl" not in result


class TestCaseInsensitive:
    """Replacements should be case-insensitive."""

    @pytest.mark.parametrize("variant", ["KEDA", "keda", "Keda"])
    def test_case_insensitive(self, variant):
        result = apply_pronunciations(f"Scaling with {variant} is great")
        assert "keh-dah" in result


class TestWholeWordOnly:
    """Whole-word matching: 'Dapr' is replaced but substrings like 'DaprClient' are not mangled."""

    def test_dapr_standalone_replaced(self):
        result = apply_pronunciations("Dapr simplifies microservices")
        assert "dapper" in result
        assert "Dapr" not in result

    def test_dapr_in_compound_not_mangled(self):
        result = apply_pronunciations("Import DaprClient from the SDK")
        # DaprClient should not become "dapperClient" — it should stay intact
        # because the \b boundary won't match mid-word
        assert "DaprClient" in result


class TestUnknownTermsUnchanged:
    """Terms not in the pronunciation dictionary pass through untouched."""

    def test_unknown_terms_unchanged(self):
        text = "We use Terraform and Pulumi for IaC"
        result = apply_pronunciations(text)
        assert "Terraform" in result
        assert "Pulumi" in result


class TestEmptyString:
    """Empty input should return empty output."""

    def test_empty_string(self):
        assert apply_pronunciations("") == ""


class TestURLsProtected:
    """URLs should not have terms inside them replaced."""

    def test_urls_protected(self):
        text = "Check https://learn.microsoft.com/AKS for docs about AKS"
        result = apply_pronunciations(text)
        # AKS inside the URL must stay as-is
        assert "https://learn.microsoft.com/AKS" in result
        # The standalone AKS outside the URL should be replaced
        assert result.endswith("A-K-S")

    def test_complex_url_preserved(self):
        text = "Visit https://github.com/Azure/AKS-Engine/CLI for CLI info"
        result = apply_pronunciations(text)
        assert "https://github.com/Azure/AKS-Engine/CLI" in result


class TestInlineCodeProtected:
    """Backtick-delimited inline code should not be modified."""

    def test_inline_code_protected(self):
        text = "Run `kubectl apply -f deploy.yaml` to deploy"
        result = apply_pronunciations(text)
        assert "`kubectl apply -f deploy.yaml`" in result

    def test_inline_code_with_multiple_terms(self):
        text = "Use `az aks get-credentials` then kubectl"
        result = apply_pronunciations(text)
        # Code block preserved
        assert "`az aks get-credentials`" in result
        # Bare kubectl outside backticks is replaced
        assert "kube-control" in result
