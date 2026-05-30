# Infra Scripts & Pre-rendered Templates

## push-images.ps1

Builds all container images from source on Azure Container Registry (no local Docker needed) and tags them under `2026-mvp-lab/`.

```powershell
.\push-images.ps1 acateam
```

Run this whenever source code changes to update the pre-built images.

## pre-rendered/quick-lab-deploy.bicep

Standalone Bicep for lab vendor provisioning. Pulls pre-built images directly from `acateam.azurecr.io` (anonymous pull) — no ACR, no source builds, no azd required.

```bash
az group create -n rg-lab-42 -l swedencentral
az deployment group create -g rg-lab-42 -f infra/pre-rendered/quick-lab-deploy.bicep -p environmentName=lab-42
```

Deploys in ~3 min. To regenerate after `main.bicep` changes, re-derive it from `main.bicep` with full-mode resources stripped and images hardcoded.
