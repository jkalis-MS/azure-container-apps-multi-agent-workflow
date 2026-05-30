"""Quick test script for the sandbox-enabled research pipeline."""
import httpx
import json

r = httpx.post("http://localhost:8001/a2a", json={
    "jsonrpc": "2.0",
    "method": "tasks/send",
    "params": {
        "id": "test-001",
        "message": {
            "parts": [
                {"kind": "text", "text": "Azure Container Apps Sandboxes for developers"},
                {"kind": "data", "data": {"topic": "Azure Container Apps Sandboxes for developers", "use_sandboxes": True}}
            ]
        }
    },
    "id": "req-test"
}, timeout=180)

data = r.json()

if "error" in data:
    print(f"ERROR: {data['error']}")
else:
    brief = data.get("result", {}).get("artifacts", [{}])[0].get("parts", [{}])[0].get("data", {})
    print("=== RESEARCH BRIEF ===")
    print(f"Topic: {brief.get('topic')}")
    print(f"Audience: {brief.get('audience')}")
    print(f"Total sources: {brief.get('total_sources')}")
    print(f"Sources with content: {brief.get('sources_with_content')}")
    print(f"Source counts: {json.dumps(brief.get('source_counts', {}), indent=2)}")
    print(f"Sandbox mode: {brief.get('sandbox_mode')}")
    print(f"Sandbox results: {json.dumps(brief.get('sandbox_results', []), indent=2)}")
    print(f"Egress violations: {json.dumps(brief.get('egress_violations', []), indent=2)}")
    print(f"Summary: {brief.get('summary')}")
