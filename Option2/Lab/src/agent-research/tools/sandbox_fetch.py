"""ACA Sandbox-based content fetching with parallel execution and egress policies."""

from __future__ import annotations

import asyncio
import json
import os
import random
from urllib.parse import urlparse

from opentelemetry import trace

_tracer = trace.get_tracer("research-agent")


def group_urls_by_domain(urls: list[str]) -> dict[str, list[str]]:
    """Group URLs by their registered domain for sandbox isolation."""
    groups: dict[str, list[str]] = {}
    for url in urls:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        # Normalize common subdomains into parent domain groups
        if domain.endswith("microsoft.com"):
            # Group learn.microsoft.com, azure.microsoft.com, etc. separately
            key = domain
        elif domain.endswith("github.com"):
            key = "github.com"
        else:
            key = domain
        groups.setdefault(key, []).append(url)
    return groups


def generate_fetch_script(urls: list[str]) -> str:
    """Generate a self-contained Python script to fetch and extract content from URLs.

    The script prints a JSON array of results to stdout.
    """
    urls_json = json.dumps(urls)
    return f'''#!/usr/bin/env python3
"""Auto-generated fetch script for ACA Sandbox execution."""
import json
import sys

import requests
from bs4 import BeautifulSoup


def fetch_url(url: str) -> dict:
    """Fetch a URL and extract main text content."""
    try:
        resp = requests.get(url, timeout=15, headers={{"User-Agent": "Mozilla/5.0 (Research-Agent)"}})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        # Remove non-content elements
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        # Find main content
        main = soup.find("main") or soup.find("article") or soup.find(attrs={{"role": "main"}}) or soup.body
        text = main.get_text(separator="\\n", strip=True) if main else ""
        # Cap content length
        text = text[:3000]
        return {{"url": url, "content": text, "chars": len(text), "error": None}}
    except Exception as e:
        return {{"url": url, "content": "", "chars": 0, "error": str(e)}}


urls = {urls_json}
results = [fetch_url(u) for u in urls]
print(json.dumps(results))
'''


def inject_hallucination(script: str, topic: str) -> tuple[str, bool]:
    """Inject a disallowed URL fetch (bing.com) to simulate agent hallucination.

    This function injects unconditionally and should only be called for the
    single sandbox selected as the hallucination target.

    Returns (modified_script, was_injected).
    """
    # Inject a bing.com fetch right before the results line
    hallucinated_line = f'''
# --- Hallucinated code (simulated agent error) ---
try:
    _hallucinated = requests.get("https://bing.com/search?q={topic}", timeout=5)
    print(f"HALLUCINATION: fetched bing.com ({{_hallucinated.status_code}})", file=sys.stderr)
except Exception as _e:
    print(f"HALLUCINATION_BLOCKED: {{_e}}", file=sys.stderr)
# --- End hallucinated code ---
'''
    # Insert before the final results collection
    script = script.replace(
        "results = [fetch_url(u) for u in urls]",
        hallucinated_line + "\nresults = [fetch_url(u) for u in urls]",
    )
    return script, True


async def create_sandbox_with_egress(
    client,
    domains: list[str],
    research_url: str,
    bing_access_attempt: bool = False,
):
    """Create a sandbox with egress policy restricting outbound to specified domains.

    Args:
        client: SandboxGroupClient (async)
        domains: List of domains to allow (e.g., ["learn.microsoft.com"])
        research_url: The URL/domain this sandbox is researching (used as a tag)
        bing_access_attempt: If True, this sandbox will attempt bing.com (tag only)

    Returns:
        (SandboxClient, allowed_domains, sandbox_id) tuple
    """
    from azure.containerapps.sandbox import EgressPolicy, EgressHostRule

    # Sanitize tag values for Azure label constraints
    def _sanitize_label(value: str, max_len: int = 63) -> str:
        cleaned = "".join(c if c.isalnum() or c in "._-" else "-" for c in value)
        return cleaned[:max_len].strip("-._") or "unknown"

    poller = await client.begin_create_sandbox(
        disk="python-3.12",
        labels={
            "url": _sanitize_label(research_url),
            "bing_access_attempt": str(bing_access_attempt).lower(),
        },
    )
    sandbox = await poller.result()

    # Build egress policy: deny all, allow only specified domains
    host_rules = []
    allowed_domains = list(domains)  # Track what we allow for reporting
    for domain in domains:
        # Allow the domain and all subdomains
        host_rules.append(EgressHostRule(pattern=f"*.{domain}", action="Allow"))
        host_rules.append(EgressHostRule(pattern=domain, action="Allow"))
    # Always allow PyPI for pip install and Ubuntu repos for apt
    host_rules.append(EgressHostRule(pattern="*.pypi.org", action="Allow"))
    host_rules.append(EgressHostRule(pattern="*.pythonhosted.org", action="Allow"))
    host_rules.append(EgressHostRule(pattern="pypi.org", action="Allow"))
    host_rules.append(EgressHostRule(pattern="*.ubuntu.com", action="Allow"))
    host_rules.append(EgressHostRule(pattern="*.debian.org", action="Allow"))

    await sandbox.set_egress_policy(EgressPolicy(
        default_action="Deny",
        host_rules=host_rules,
        traffic_inspection="Full",
    ))

    sandbox_id = getattr(sandbox, "name", None) or getattr(sandbox, "id", None) or ""
    return sandbox, allowed_domains, sandbox_id


async def execute_in_sandbox(sandbox, script: str) -> dict:
    """Write and execute a fetch script in the sandbox, returning parsed results.

    Returns:
        {
            "results": [...],  # Parsed JSON from stdout
            "stderr": str,     # Stderr output (includes hallucination logs)
            "exit_code": int,
            "install_exit_code": int,
            "install_stderr": str,
        }
    """
    # Install dependencies
    install_result = await sandbox.exec("pip install requests beautifulsoup4 --quiet")
    if install_result.exit_code != 0:
        print(f"[Sandbox] pip install failed (exit {install_result.exit_code}): {install_result.stderr}")

    # Write the script
    await sandbox.write_file("/tmp/fetch.py", script)

    # Execute
    result = await sandbox.exec("python3 /tmp/fetch.py")

    parsed_results = []
    if result.exit_code == 0 and result.stdout.strip():
        try:
            parsed_results = json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            print(f"[Sandbox] JSON parse failed for stdout: {result.stdout[:200]}")
    elif result.exit_code != 0:
        print(f"[Sandbox] Script failed (exit {result.exit_code}): {result.stderr}")
        if result.stdout:
            print(f"[Sandbox] Script stdout: {result.stdout[:200]}")

    return {
        "results": parsed_results,
        "stderr": result.stderr or "",
        "exit_code": result.exit_code,
        "install_exit_code": install_result.exit_code,
        "install_stderr": install_result.stderr or "",
    }


async def fetch_with_sandboxes(
    urls: list[str],
    topic: str,
    status_callback=None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Orchestrate parallel sandbox-based content fetching.

    Creates one sandbox per domain group, configures egress policies,
    executes fetch scripts in parallel, and returns aggregated results.

    Args:
        urls: List of URLs to fetch (already ranked)
        topic: Research topic (used for hallucination injection)

    Returns:
        (fetched_content, egress_violations, sandbox_statuses) tuple
    """
    from azure.identity.aio import DefaultAzureCredential
    from azure.containerapps.sandbox.aio import SandboxGroupClient
    from azure.containerapps.sandbox import endpoint_for_region

    subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
    resource_group = os.environ.get("ACA_SANDBOX_RESOURCE_GROUP", "")
    sandbox_group = os.environ.get("ACA_SANDBOX_GROUP_NAME", "")
    region = os.environ.get("ACA_SANDBOXGROUP_REGION", "eastus2")

    if not all([subscription_id, resource_group, sandbox_group]):
        raise ValueError(
            "Missing required env vars: AZURE_SUBSCRIPTION_ID, "
            "ACA_SANDBOX_RESOURCE_GROUP, ACA_SANDBOX_GROUP_NAME"
        )

    # Group URLs by domain
    domain_groups = group_urls_by_domain(urls)

    # Split learn.microsoft.com across two sandboxes for parallelism (it tends to be large)
    if "learn.microsoft.com" in domain_groups and len(domain_groups["learn.microsoft.com"]) >= 2:
        learn_urls = domain_groups.pop("learn.microsoft.com")
        mid = (len(learn_urls) + 1) // 2
        domain_groups["learn.microsoft.com (1/2)"] = learn_urls[:mid]
        domain_groups["learn.microsoft.com (2/2)"] = learn_urls[mid:]

    print(f"[Sandbox] Grouped {len(urls)} URLs into {len(domain_groups)} domain groups: {list(domain_groups.keys())}")

    # Cap at 5 concurrent sandboxes
    max_sandboxes = 5
    if len(domain_groups) > max_sandboxes:
        # Merge smallest groups together
        sorted_groups = sorted(domain_groups.items(), key=lambda x: len(x[1]), reverse=True)
        merged = dict(sorted_groups[:max_sandboxes - 1])
        overflow_urls = []
        for _, group_urls in sorted_groups[max_sandboxes - 1:]:
            overflow_urls.extend(group_urls)
        if overflow_urls:
            merged["_overflow"] = overflow_urls
        domain_groups = merged

    credential = DefaultAzureCredential()
    client = SandboxGroupClient(
        endpoint_for_region(region),
        credential,
        subscription_id=subscription_id,
        resource_group=resource_group,
        sandbox_group=sandbox_group,
    )

    sandboxes = []
    all_results: list[dict] = []
    egress_violations: list[dict] = []
    sandbox_statuses: list[dict] = []
    live_statuses: list[dict] = []

    async def _notify_status() -> None:
        """Push a status snapshot to caller for live UI updates."""
        if status_callback is not None:
            await status_callback([dict(s) for s in live_statuses])

    try:
        # Pre-select the hallucination target sandbox (biased to slots 1-3 for demo).
        group_names = list(domain_groups.keys())
        preferred_pool = list(range(min(3, len(group_names))))
        hallucination_idx = random.choice(preferred_pool) if preferred_pool else None
        if hallucination_idx is not None:
            print(f"[Sandbox] Hallucination target sandbox index selected: {hallucination_idx + 1}")

        # Create all sandboxes in parallel
        with _tracer.start_as_current_span(
            "sandbox_create_parallel",
            attributes={"sandbox.count": len(domain_groups)},
        ):
            create_tasks = []
            for i, (domain, group_urls) in enumerate(domain_groups.items()):
                is_hallucination_target = (i == hallucination_idx)
                # Extract unique domains for egress rules
                egress_domains = list(set(
                    urlparse(u).netloc for u in group_urls
                ))
                research_url_tag = group_urls[0] if group_urls else domain
                create_tasks.append(
                    create_sandbox_with_egress(
                        client,
                        egress_domains,
                        research_url=research_url_tag,
                        bing_access_attempt=is_hallucination_target,
                    )
                )
            sandboxes = await asyncio.gather(*create_tasks, return_exceptions=True)

        # Execute fetch scripts in parallel
        with _tracer.start_as_current_span(
            "sandbox_execute_parallel",
            attributes={"sandbox.count": len(domain_groups)},
        ):
            exec_tasks = []
            status_indices = []
            for i, (domain, group_urls) in enumerate(domain_groups.items()):
                sandbox_result = sandboxes[i]
                live_status = {
                    "domain": domain,
                    "sandbox_id": "",
                    "urls_count": len(group_urls),
                    "status": "active",
                    "progress_state": "running",
                    "hallucination_target": (i == hallucination_idx),
                    "egress_blocked": False,
                    "allowed_domains": [],
                    "egress_denied": [],
                }
                live_statuses.append(live_status)
                status_idx = len(live_statuses) - 1
                await _notify_status()

                if isinstance(sandbox_result, Exception):
                    print(f"[Sandbox] Failed to create sandbox for {domain}: {sandbox_result}")
                    live_statuses[status_idx].update({
                        "urls_count": 0,
                        "status": "create_failed",
                        "progress_state": "finished",
                        "error": str(sandbox_result)[:200],
                    })
                    sandbox_statuses.append(dict(live_statuses[status_idx]))
                    await _notify_status()
                    continue

                sandbox, allowed_domains, sandbox_id = sandbox_result
                print(f"[Sandbox] Created sandbox {sandbox_id} for {domain} ({len(group_urls)} URLs, egress: {allowed_domains})")
                script = generate_fetch_script(group_urls)
                is_hallucination_target = (i == hallucination_idx)
                if is_hallucination_target:
                    script, was_injected = inject_hallucination(script, topic)
                    if was_injected:
                        print(f"[Sandbox] Injected hallucination into sandbox for domain: {domain}")

                live_statuses[status_idx]["allowed_domains"] = allowed_domains
                live_statuses[status_idx]["sandbox_id"] = sandbox_id
                await _notify_status()
                exec_tasks.append(asyncio.create_task(
                    _execute_and_collect(sandbox, script, domain, is_hallucination_target, allowed_domains, sandbox_id)
                ))
                status_indices.append(status_idx)

            async def _execute_with_index(status_index: int, task: asyncio.Task):
                try:
                    return status_index, await task
                except Exception as ex:
                    return status_index, ex

            indexed_tasks = [
                asyncio.create_task(_execute_with_index(status_idx, task))
                for status_idx, task in zip(status_indices, exec_tasks)
            ]

            for completed in asyncio.as_completed(indexed_tasks):
                status_idx, result = await completed
                if isinstance(result, Exception):
                    print(f"[Sandbox] Execution error: {result}")
                    live_statuses[status_idx].update({
                        "status": "exec_error",
                        "progress_state": "finished",
                        "error": str(result)[:200],
                        "egress_blocked": False,
                    })
                    sandbox_statuses.append(dict(live_statuses[status_idx]))
                    await _notify_status()
                    continue

                fetched, violations, status = result
                all_results.extend(fetched)
                egress_violations.extend(violations)
                status["progress_state"] = "finished"
                live_statuses[status_idx] = dict(status)
                sandbox_statuses.append(dict(status))
                await _notify_status()

    finally:
        # Clean up all sandboxes
        cleanup_tasks = []
        for sandbox_result in sandboxes:
            if not isinstance(sandbox_result, Exception):
                sandbox = sandbox_result[0]
                cleanup_tasks.append(_safe_delete(sandbox))
        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)
        await client.close()
        await credential.close()

    return all_results, egress_violations, sandbox_statuses


async def _execute_and_collect(
    sandbox, script: str, domain: str, is_hallucination_target: bool, allowed_domains: list[str], sandbox_id: str = ""
) -> tuple[list[dict], list[dict], dict]:
    """Execute script in sandbox and collect results + egress violations + status."""
    exec_result = await execute_in_sandbox(sandbox, script)

    fetched = exec_result["results"]
    violations = []

    # Log stderr for debugging hallucination
    if exec_result["stderr"]:
        print(f"[Sandbox] stderr for {domain}: {exec_result['stderr'][:300]}")

    # Always fetch egress decisions to see what was allowed/denied
    egress_denied = []
    egress_allowed_count = 0
    try:
        decisions = await sandbox.get_egress_decisions()
        # EgressDecisions has .network_egress (NetworkEgressDecisions) with .allowed/.denied lists
        if decisions and decisions.network_egress:
            net = decisions.network_egress
            # Process denied entries
            if net.denied:
                for entry in net.denied:
                    host = getattr(entry, 'host', 'unknown')
                    egress_denied.append(host)
                    violations.append({
                        "domain": domain,
                        "blocked_host": host,
                        "reason": "egress_policy_deny",
                        "detail": f"Outbound request to {host} blocked by sandbox egress policy",
                    })
            # Count allowed entries
            if net.allowed:
                egress_allowed_count = len(net.allowed)
            print(f"[Sandbox] Egress decisions for {domain}: {egress_allowed_count} allowed, {len(egress_denied)} denied")
        else:
            print(f"[Sandbox] No egress decisions available for {domain}")
    except Exception as e:
        print(f"[Sandbox] get_egress_decisions() failed for {domain}: {e}")

    # Also check stderr for hallucination evidence (blocked OR detected unauthorized)
    if is_hallucination_target and "HALLUCINATION_BLOCKED" in exec_result["stderr"]:
        if "bing.com" not in egress_denied:
            egress_denied.append("bing.com")
            violations.append({
                "domain": domain,
                "blocked_host": "bing.com",
                "reason": "egress_policy_deny",
                "detail": "Agent attempted to fetch bing.com — BLOCKED by sandbox egress policy (only " + ", ".join(allowed_domains) + " allowed)",
            })
    elif is_hallucination_target and "HALLUCINATION:" in exec_result["stderr"]:
        # The request went through but was unauthorized — report as detected violation
        if "bing.com" not in egress_denied:
            egress_denied.append("bing.com")
            violations.append({
                "domain": domain,
                "blocked_host": "bing.com",
                "reason": "egress_audit_violation",
                "detail": "Agent fetched bing.com OUTSIDE allowed domains — detected by sandbox egress audit (allowed: " + ", ".join(allowed_domains) + ")",
            })

    # Build per-sandbox status metadata
    status = {
        "domain": domain,
        "sandbox_id": sandbox_id,
        "urls_count": len(fetched),
        "exit_code": exec_result["exit_code"],
        "install_exit_code": exec_result["install_exit_code"],
        "egress_blocked": len(egress_denied) > 0,
        "hallucination_target": is_hallucination_target,
        "allowed_domains": allowed_domains,
        "egress_denied": egress_denied,
        "egress_allowed_count": egress_allowed_count,
        "status": "success" if fetched else ("egress_blocked" if violations else "failed"),
    }
    if exec_result["exit_code"] != 0:
        status["error"] = (exec_result["stderr"] or "")[:200]
    if exec_result["install_exit_code"] != 0:
        status["install_error"] = (exec_result["install_stderr"] or "")[:200]

    return fetched, violations, status


async def _safe_delete(sandbox):
    """Delete a sandbox, ignoring errors."""
    try:
        await sandbox.delete()
    except Exception:
        pass
