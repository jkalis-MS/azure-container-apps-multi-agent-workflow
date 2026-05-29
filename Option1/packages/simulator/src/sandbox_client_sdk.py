"""
SDK-based client for Azure Container Apps Sandboxes.

This is an alternative implementation of sandbox_client.py that uses the official
Azure Container Apps Sandbox Python SDK (azure-containerapps-sandbox) instead of
raw REST API calls.

To switch to this implementation, change the import in agent.py:
    from .sandbox_client_sdk import SandboxClient, ExecResult
    
To revert, change back to:
    from .sandbox_client import SandboxClient, ExecResult

Install the SDK:
    pip install https://github.com/microsoft/azure-container-apps/releases/download/python-sdk-v0.1.0b1-early-access/azure_containerapps_sandbox-0.1.0b1-py3-none-any.whl
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from azure.identity.aio import DefaultAzureCredential, ClientSecretCredential
from azure.containerapps.sandbox.aio import SandboxGroupClient
from azure.containerapps.sandbox import endpoint_for_region

logger = logging.getLogger("simulator.sandbox")

# Region for the sandbox group (used to resolve the data-plane endpoint)
REGION = os.environ.get("ACA_SANDBOX_REGION", "westcentralus")


@dataclass
class ExecResult:
    """Matches the ExecResult from the original sandbox_client.py for compatibility."""
    exit_code: int
    stdout: str
    stderr: str
    execution_time_ms: int = 0


class SandboxClient:
    """Async client for Container Apps Sandboxes using the official Python SDK.
    
    This is a drop-in replacement for the raw REST client in sandbox_client.py.
    It maintains the same interface so agent.py doesn't need changes.
    """

    def __init__(
        self,
        subscription_id: str | None = None,
        resource_group: str | None = None,
        sandbox_group: str | None = None,
    ):
        self.subscription_id = subscription_id or os.environ["AZURE_SUBSCRIPTION_ID"]
        self.resource_group = resource_group or os.environ["ACA_RESOURCE_GROUP"]
        self.sandbox_group = sandbox_group or os.environ["ACA_SANDBOX_GROUP"]

        # Auth: service principal if env vars are set, otherwise DefaultAzureCredential
        client_id = os.environ.get("AZURE_CLIENT_ID")
        tenant_id = os.environ.get("AZURE_TENANT_ID")
        client_secret = os.environ.get("AZURE_CLIENT_SECRET")
        if client_id and tenant_id and client_secret:
            self._credential = ClientSecretCredential(tenant_id, client_id, client_secret)
        else:
            self._credential = DefaultAzureCredential()

        self._group_client: SandboxGroupClient | None = None
        # Cache of sandbox_id → SDK SandboxClient for exec/file operations
        self._sandbox_clients: dict[str, Any] = {}
        # Value to inject via egress transform (resolved from env, avoids passing to sandbox)
        self._transform_secret_value: str | None = os.environ.get("AZURE_OPENAI_KEY")
        if self._transform_secret_value:
            logger.info(f"SandboxClient: AZURE_OPENAI_KEY present ({len(self._transform_secret_value)} chars)")
        else:
            logger.warning("SandboxClient: AZURE_OPENAI_KEY not set — egress transforms will not inject api-key")

    def _get_group_client(self) -> SandboxGroupClient:
        """Lazily create the SandboxGroupClient."""
        if self._group_client is None:
            self._group_client = SandboxGroupClient(
                endpoint_for_region(REGION),
                self._credential,
                subscription_id=self.subscription_id,
                resource_group=self.resource_group,
                sandbox_group=self.sandbox_group,
            )
        return self._group_client

    async def close(self):
        """Close all SDK clients and the credential."""
        if self._group_client is not None:
            await self._group_client.close()
            self._group_client = None
        self._sandbox_clients.clear()
        if hasattr(self._credential, 'close'):
            await self._credential.close()

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
        """Create a new sandbox. Returns the server-assigned sandbox ID.
        
        The SDK's begin_create_sandbox doesn't accept egress policies at creation
        time, so we create the sandbox first, then set the egress policy.
        """
        logger.info(f"Creating sandbox via SDK (disk={disk_image}, cpu={cpu}, mem={memory})")
        group = self._get_group_client()

        poller = await group.begin_create_sandbox(
            disk=disk_image,
            cpu=cpu,
            memory=memory,
        )
        sandbox = await poller.result()
        sandbox_id = sandbox.sandbox_id
        logger.info(f"Sandbox created: {sandbox_id}")

        # Cache the SandboxClient for later exec/file operations
        self._sandbox_clients[sandbox_id] = sandbox

        # Apply egress policy if provided
        if egress_policy:
            await self._apply_egress_policy(sandbox, egress_policy)

        return sandbox_id

    async def _apply_egress_policy(self, sandbox: Any, policy: dict[str, Any]) -> None:
        """Apply egress policy to a sandbox by posting the full policy dict directly.
        
        We bypass the SDK's individual helper methods (add_egress_host_rule,
        add_egress_transform_rule) because the SDK's EgressHeader model doesn't
        support valueRef (secret reference). Instead we build the full policy payload
        and POST it to the data-plane endpoint directly.
        
        For Transform rules with valueRef, creates the group-level secret first.
        """
        # Build the API payload
        api_policy: dict[str, Any] = {
            "defaultAction": policy.get("defaultAction", "Allow"),
            "hostRules": [],
            "rules": [],
        }
        if policy.get("trafficInspection"):
            api_policy["trafficInspection"] = policy["trafficInspection"]

        for rule in policy.get("rules", []):
            action_data = rule.get("action", {})
            action_type = action_data.get("type", "Deny")
            match_data = rule.get("match", {})
            host_pattern = match_data.get("host", "")
            rule_name = rule.get("name", "unnamed")

            logger.info(f"  Egress rule: {rule_name} ({action_type}) for {host_pattern}")

            if action_type in ("Deny", "Allow"):
                # Use advanced rules format so they appear in portal
                api_policy["rules"].append({
                    "name": rule_name,
                    "match": {"host": host_pattern},
                    "action": {"type": action_type},
                })
            elif action_type == "Transform":
                # For secret-based headers, upsert the group secret first
                raw_headers = action_data.get("headers", [])
                api_headers = []
                for h in raw_headers:
                    value_ref = h.get("valueRef")
                    if value_ref:
                        secret_ref = value_ref.get("secretRef", {})
                        secret_id = secret_ref.get("secretId", "")
                        secret_key = secret_ref.get("secretKey", "")
                        if secret_id and self._transform_secret_value:
                            group = self._get_group_client()
                            await group.upsert_secret(secret_id, {secret_key: self._transform_secret_value})
                            logger.info(f"  Upserted group secret '{secret_id}' (key: '{secret_key}')")
                        api_headers.append({
                            "operation": h.get("operation", "Set"),
                            "name": h.get("name", ""),
                            "valueRef": value_ref,
                        })
                    elif "value" in h:
                        api_headers.append({
                            "operation": h.get("operation", "Set"),
                            "name": h.get("name", ""),
                            "value": h["value"],
                        })
                api_policy["rules"].append({
                    "name": rule_name,
                    "match": {"host": host_pattern},
                    "action": {"type": "Transform", "headers": api_headers},
                })

        # Ensure sandbox is running (egress endpoint requires it)
        await sandbox.ensure_running()

        # POST the full policy directly to the data-plane endpoint
        sandbox_path = sandbox._sbx_path
        result = await sandbox._dp_post(f"{sandbox_path}/egresspolicy", api_policy)
        logger.info(f"  Egress policy applied ✓ (response: {str(result)[:100]})")

    def _get_sandbox(self, sandbox_id: str) -> Any:
        """Get the cached SDK SandboxClient for a sandbox ID."""
        if sandbox_id in self._sandbox_clients:
            return self._sandbox_clients[sandbox_id]
        # If not cached (e.g., from a previous session), create a client wrapper
        group = self._get_group_client()
        client = group.get_sandbox_client(sandbox_id)
        self._sandbox_clients[sandbox_id] = client
        return client

    async def get_sandbox(self, sandbox_id: str) -> dict:
        """Get sandbox details."""
        try:
            sandbox = self._get_sandbox(sandbox_id)
            info = await sandbox.get()
            return {"id": info.id, "state": info.state}
        except Exception as e:
            logger.warning(f"Get sandbox failed: {e}")
            return {}

    async def list_sandboxes(self) -> list[dict]:
        """List all sandboxes in the group."""
        group = self._get_group_client()
        result = []
        async for s in group.list_sandboxes():
            result.append({"id": s.id, "state": s.state})
        return result

    async def delete_sandbox(self, sandbox_id: str) -> None:
        """Delete a sandbox."""
        logger.info(f"Deleting sandbox {sandbox_id}")
        try:
            sandbox = self._get_sandbox(sandbox_id)
            await sandbox.delete()
            self._sandbox_clients.pop(sandbox_id, None)
        except Exception as e:
            logger.warning(f"Delete failed: {e}")

    # -- exec ---------------------------------------------------------------

    async def exec_command(
        self, sandbox_id: str, command: str, working_dir: str | None = None
    ) -> ExecResult:
        """Execute a shell command inside a sandbox."""
        logger.info(f"Exec in {sandbox_id}: {command[:80]}...")
        sandbox = self._get_sandbox(sandbox_id)
        try:
            result = await sandbox.exec(command)
            return ExecResult(
                exit_code=result.exit_code,
                stdout=result.stdout or "",
                stderr=result.stderr or "",
                execution_time_ms=0,
            )
        except Exception as e:
            logger.error(f"Exec failed: {e}")
            return ExecResult(exit_code=-1, stdout="", stderr=str(e))

    async def exec_python(
        self, sandbox_id: str, script: str
    ) -> ExecResult:
        """Write a Python script into the sandbox and execute it.
        
        Uses the SDK's write_file to write the script, then exec to run it.
        This is cleaner than the heredoc approach in the raw REST client.
        """
        sandbox = self._get_sandbox(sandbox_id)
        try:
            await sandbox.write_file("/tmp/run_script.py", script)
            result = await sandbox.exec("python3 /tmp/run_script.py")
            return ExecResult(
                exit_code=result.exit_code,
                stdout=result.stdout or "",
                stderr=result.stderr or "",
                execution_time_ms=0,
            )
        except Exception as e:
            logger.error(f"exec_python failed: {e}")
            return ExecResult(exit_code=-1, stdout="", stderr=str(e))

    async def get_egress_decisions(self, sandbox_id: str) -> list[dict]:
        """Get egress decisions (traffic log) for a sandbox."""
        sandbox = self._get_sandbox(sandbox_id)
        try:
            decisions = await sandbox.get_egress_decisions()
            # SDK returns objects — convert to dicts for compatibility
            if isinstance(decisions, list):
                return [d if isinstance(d, dict) else vars(d) for d in decisions]
            return []
        except Exception as e:
            logger.warning(f"Get egress decisions failed: {e}")
            return []

    # -- file operations (using SDK's built-in file support) ----------------

    async def write_file(self, sandbox_id: str, path: str, content: str) -> None:
        """Write a file inside a sandbox using the SDK's write_file method.
        
        This replaces the chunked base64 approach in _write_file_in_sandbox().
        """
        sandbox = self._get_sandbox(sandbox_id)
        await sandbox.write_file(path, content)

    async def read_file(self, sandbox_id: str, path: str) -> str:
        """Read a file from inside a sandbox."""
        sandbox = self._get_sandbox(sandbox_id)
        content = await sandbox.read_file(path)
        return content.decode() if isinstance(content, bytes) else content

    # -- secrets (group-level) ----------------------------------------------

    async def upsert_secret(self, secret_id: str, values: dict[str, str]) -> None:
        """Create or update a group-level secret."""
        group = self._get_group_client()
        await group.upsert_secret(secret_id, values)

    # -- disk images --------------------------------------------------------

    async def list_disk_images(self, public: bool = True) -> list[dict]:
        """List available disk images."""
        group = self._get_group_client()
        images = []
        if public:
            async for img in group.list_public_disk_images():
                images.append({"name": img.name, "id": img.id} if hasattr(img, 'name') else img)
        else:
            async for img in group.list_disk_images():
                images.append({"name": img.name, "id": img.id} if hasattr(img, 'name') else img)
        return images
