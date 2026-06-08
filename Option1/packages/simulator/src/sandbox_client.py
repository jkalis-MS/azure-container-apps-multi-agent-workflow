"""
REST API client for Azure Container Apps Sandboxes (preview).

This client talks to the Sandboxes DATA-PLANE API (not ARM control plane).
It creates sandboxes, executes code inside them, and reads results.

Auth: Uses a service principal (ClientSecretCredential) or DefaultAzureCredential.
Token audience: https://management.azuredevcompute.io

Key operations:
  - create_sandbox(disk_image, egress_policy) → sandbox_id
  - exec_python(sandbox_id, code) → ExecResult(stdout, stderr, exit_code)
  - exec_command(sandbox_id, command) → ExecResult
  - delete_sandbox(sandbox_id)
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from azure.identity import DefaultAzureCredential, ClientSecretCredential, ManagedIdentityCredential

logger = logging.getLogger("simulator.sandbox")

# Direct data-plane endpoint (regional)
DATA_PLANE = os.environ.get(
    "ACA_SANDBOX_ENDPOINT", "https://management.westcentralus.azuredevcompute.io"
)

# Token audience for data-plane auth
TOKEN_RESOURCE = "https://management.azuredevcompute.io"


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    execution_time_ms: int = 0


class SandboxClient:
    """Async client for the Container Apps Sandboxes data-plane API."""

    def __init__(
        self,
        subscription_id: str | None = None,
        resource_group: str | None = None,
        sandbox_group: str | None = None,
    ):
        self.subscription_id = subscription_id or os.environ["AZURE_SUBSCRIPTION_ID"]
        self.resource_group = resource_group or os.environ["ACA_RESOURCE_GROUP"]
        self.sandbox_group = sandbox_group or os.environ["ACA_SANDBOX_GROUP"]

        # Auth strategy:
        # 1. Pre-baked token via ACA_SANDBOX_TOKEN env var (for Express which has no MI)
        # 2. Service principal via AZURE_CLIENT_ID/SECRET/TENANT
        # 3. DefaultAzureCredential (picks up Managed Identity in Azure, CLI locally)
        self._static_token = os.environ.get("ACA_SANDBOX_TOKEN")
        self._credential = None
        if not self._static_token:
            client_id = os.environ.get("AZURE_CLIENT_ID")
            tenant_id = os.environ.get("AZURE_TENANT_ID")
            client_secret = os.environ.get("AZURE_CLIENT_SECRET")
            if client_id and tenant_id and client_secret:
                self._credential = ClientSecretCredential(tenant_id, client_id, client_secret)
            else:
                self._credential = DefaultAzureCredential()

        self._token: str | None = None
        self._http: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            # Create transport directly to bypass any OTEL httpx instrumentation
            transport = httpx.AsyncHTTPTransport(verify=False)
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(120.0), verify=False, transport=transport
            )
        return self._http

    async def _get_token(self) -> str:
        if self._static_token:
            return self._static_token
        token = self._credential.get_token(f"{TOKEN_RESOURCE}/.default")
        self._token = token.token
        return self._token

    async def close(self):
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    def _base_path(self) -> str:
        """Base path for sandbox group operations."""
        return (
            f"{DATA_PLANE}/subscriptions/{self.subscription_id}"
            f"/resourceGroups/{self.resource_group}"
            f"/sandboxGroups/{self.sandbox_group}"
        )

    async def _headers(self) -> dict[str, str]:
        token = await self._get_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def _request(
        self, method: str, url: str, *, body: dict | None = None
    ) -> httpx.Response:
        client = await self._ensure_client()
        headers = await self._headers()
        # Retry on transient 5xx errors
        last_resp = None
        for attempt in range(4):
            resp = await client.request(method, url, headers=headers, json=body)
            if resp.status_code < 500:
                return resp
            last_resp = resp
            wait = 2 ** attempt  # 1, 2, 4 seconds
            logger.warning(f"Sandbox API {resp.status_code} on attempt {attempt+1}, retrying in {wait}s...")
            import asyncio
            await asyncio.sleep(wait)
        return last_resp

    # -- sandbox lifecycle --------------------------------------------------

    async def create_sandbox(
        self,
        disk_image: str = "python-3.14",
        cpu: str = "1000m",
        memory: str = "2Gi",
        disk: str = "20Gi",
        auto_suspend_secs: int = 300,
        egress_policy: dict[str, Any] | None = None,
    ) -> str:
        """Create a new sandbox. Returns the server-assigned sandbox ID."""
        logger.info(f"Creating sandbox (image={disk_image}, cpu={cpu}, mem={memory})")
        body: dict[str, Any] = {
            "sourcesRef": {
                "diskImage": {"name": disk_image, "isPublic": True},
            },
            "vmmType": "CloudHypervisor",
            "resources": {"cpu": cpu, "memory": memory, "disk": disk},
            "lifecycle": {
                "autoSuspendPolicy": {
                    "enabled": True,
                    "interval": auto_suspend_secs,
                    "mode": "Memory",
                },
                "autoDeletePolicy": {
                    "enabled": False,
                    "deleteIntervalInSeconds": 86400,
                },
            },
        }
        if egress_policy:
            body["egressPolicy"] = egress_policy
        url = f"{self._base_path()}/sandboxes?includeDebug=true"
        resp = await self._request("PUT", url, body=body)
        if resp.status_code >= 400:
            logger.error(f"Create sandbox failed ({resp.status_code}): {resp.text}")
            resp.raise_for_status()
        data = resp.json()
        sandbox_id = data["id"]
        logger.info(f"Sandbox created: {sandbox_id} (state={data.get('state')})")
        return sandbox_id

    async def get_sandbox(self, sandbox_id: str) -> dict:
        """Get sandbox details."""
        url = f"{self._base_path()}/sandboxes/{sandbox_id}"
        resp = await self._request("GET", url)
        if resp.status_code >= 400:
            logger.warning(f"Get sandbox failed ({resp.status_code}): {resp.text}")
            return {}
        return resp.json()

    async def list_sandboxes(self) -> list[dict]:
        """List all sandboxes in the group."""
        url = f"{self._base_path()}/sandboxes?Page=1&PageSize=100"
        resp = await self._request("GET", url)
        if resp.status_code >= 400:
            return []
        data = resp.json()
        return data if isinstance(data, list) else data.get("value", [])

    async def delete_sandbox(self, sandbox_id: str) -> None:
        """Delete a sandbox."""
        logger.info(f"Deleting sandbox {sandbox_id}")
        url = f"{self._base_path()}/sandboxes/{sandbox_id}"
        resp = await self._request("DELETE", url)
        if resp.status_code >= 400:
            logger.warning(f"Delete failed ({resp.status_code}): {resp.text}")

    # -- exec ---------------------------------------------------------------

    async def exec_command(
        self, sandbox_id: str, command: str, working_dir: str | None = None
    ) -> ExecResult:
        """Execute a shell command inside a sandbox.

        Data-plane path: POST /sandboxes/{id}/executeShellCommand
        """
        logger.info(f"Exec in {sandbox_id}: {command[:80]}...")
        body: dict[str, Any] = {"command": command}
        if working_dir:
            body["workingDirectory"] = working_dir
        url = f"{self._base_path()}/sandboxes/{sandbox_id}/executeShellCommand"
        resp = await self._request("POST", url, body=body)
        if resp.status_code >= 400:
            logger.error(f"Exec failed ({resp.status_code}): {resp.text}")
            return ExecResult(exit_code=-1, stdout="", stderr=resp.text)
        data = resp.json()
        return ExecResult(
            exit_code=data.get("exitCode", -1),
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            execution_time_ms=data.get("executionTimeMs", 0),
        )

    async def exec_python(
        self, sandbox_id: str, script: str
    ) -> ExecResult:
        """Write a Python script into the sandbox and execute it."""
        # Use heredoc to write the script, then run it
        escaped = script.replace("'", "'\\''")
        combined = f"cat > /tmp/run_script.py << 'PYSCRIPT_EOF'\n{script}\nPYSCRIPT_EOF\npython3 /tmp/run_script.py"
        return await self.exec_command(sandbox_id, combined)

    async def get_egress_decisions(self, sandbox_id: str) -> list[dict]:
        """Get egress decisions (traffic log) for a sandbox."""
        url = f"{self._base_path()}/sandboxes/{sandbox_id}/egressDecisions"
        resp = await self._request("GET", url)
        if resp.status_code >= 400:
            logger.warning(f"Get egress decisions failed ({resp.status_code}): {resp.text}")
            return []
        data = resp.json()
        return data if isinstance(data, list) else data.get("value", data.get("decisions", []))

    # -- secrets (group-level) ----------------------------------------------

    async def upsert_secret(self, secret_id: str, values: dict[str, str]) -> None:
        """Create or update a group-level secret."""
        url = f"{self._base_path()}/secrets/{secret_id}"
        body = {"values": values}
        resp = await self._request("PUT", url, body=body)
        if resp.status_code >= 400:
            logger.warning(f"Upsert secret failed ({resp.status_code}): {resp.text}")
        else:
            logger.info(f"Secret '{secret_id}' upserted ✓")

    # -- disk images --------------------------------------------------------

    async def list_disk_images(self, public: bool = True) -> list[dict]:
        """List available disk images."""
        kind = "public" if public else ""
        url = f"{self._base_path()}/diskimages/{kind}?Page=1&PageSize=100"
        resp = await self._request("GET", url)
        if resp.status_code >= 400:
            return []
        data = resp.json()
        return data if isinstance(data, list) else data.get("value", [])
