# Infra

Bicep templates and supporting scripts for deploying the lab to Azure.

## main.bicep

Primary deployment template. Provisions the Container Apps environment, Foundry/AI Services account with `gpt-4o` + `tts-1` deployments, Application Insights, Log Analytics, ACR, Storage, and all four container apps (`agent-research`, `agent-creator`, `agent-podcaster`, `dev-ui`, `tts-server`).

Deploy with [Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/) from the `Option2/` folder:

```bash
cd Option2
azd up
```

`azd` handles parameter prompting (subscription, region), container builds (`remoteBuild: true`), and post-provision deployment of the apps.

## push-images.ps1

Builds all container images from source on Azure Container Registry (no local Docker needed) and tags them under `2026-mvp-lab/`. Useful for publishing shared lab images.

```powershell
.\push-images.ps1 acateam
```
