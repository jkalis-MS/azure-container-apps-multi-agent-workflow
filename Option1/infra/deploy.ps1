#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Build and deploy all services to Azure Container Apps.
.DESCRIPTION
  Builds container images via ACR Tasks and updates Container Apps.
  Cleans up all sandboxes before deploying.
  Requires: az CLI logged in, ACR admin enabled.
.PARAMETER Services
  Which services to deploy. Default: all.
#>
param(
  [ValidateSet("all","orchestrator","simulator","blog-generator","narration-generator")]
  [string[]]$Services = @("all"),
  [string]$ResourceGroup = $env:ACA_RESOURCE_GROUP,
  [string]$Registry = $env:ACR_NAME,
  [string]$SandboxGroup = $env:ACA_SANDBOX_GROUP,
  [string]$SubscriptionId = $env:AZURE_SUBSCRIPTION_ID
)

$ErrorActionPreference = "Stop"
$tag = "v3-$(Get-Date -Format 'yyyyMMddHHmmss')"

# ---------------------------------------------------------------------------
# Cleanup: Delete all sandboxes
# ---------------------------------------------------------------------------
Write-Host "`n━━━ Cleaning up sandboxes ━━━" -ForegroundColor Magenta
try {
  $sbToken = az account get-access-token --resource "https://management.azuredevcompute.io" --query accessToken -o tsv
  $sbBase = "https://management.westcentralus.azuredevcompute.io/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup/sandboxGroups/$SandboxGroup"
  $headers = @{ Authorization = "Bearer $sbToken"; "Content-Type" = "application/json" }

  $sandboxes = Invoke-RestMethod -Uri "$sbBase/sandboxes?Page=1&PageSize=100" -Headers $headers -SkipCertificateCheck
  if ($sandboxes.Count -gt 0) {
    foreach ($sb in $sandboxes) {
      $sbId = $sb.id
      Write-Host "  Deleting sandbox $sbId..."
      Invoke-RestMethod -Uri "$sbBase/sandboxes/$sbId" -Method DELETE -Headers $headers -SkipCertificateCheck | Out-Null
    }
    Write-Host "  ✅ Deleted $($sandboxes.Count) sandbox(es)" -ForegroundColor Green
  } else {
    Write-Host "  No sandboxes to clean up" -ForegroundColor DarkGray
  }
} catch {
  Write-Host "  ⚠️  Sandbox cleanup failed (non-fatal): $_" -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# Build and deploy services
# ---------------------------------------------------------------------------
$serviceMap = @{
  "orchestrator"        = @{ path = "packages/orchestrator";        app = "wc-orchestrator";    image = "wc-orchestrator" }
  "simulator"           = @{ path = "packages/simulator";           app = "wc-simulator-mi";    image = "wc-simulator-mi" }
  "blog-generator"      = @{ path = "packages/blog-generator";      app = "wc-blog-gen";        image = "wc-blog-gen" }
  "narration-generator" = @{ path = "packages/narration-generator"; app = "wc-narration-gen";   image = "wc-narration-gen" }
}

$toDeploy = if ($Services -contains "all") { $serviceMap.Keys } else { $Services }

foreach ($svc in $toDeploy) {
  $info = $serviceMap[$svc]
  $imageFull = "$Registry.azurecr.io/$($info.image):$tag"
  
  Write-Host "`n━━━ Building $svc → $imageFull ━━━" -ForegroundColor Cyan
  az acr build --registry $Registry --image "$($info.image):$tag" $info.path --no-logs
  if ($LASTEXITCODE -ne 0) { Write-Error "ACR build failed for $svc"; continue }
  
  Write-Host "━━━ Deploying $svc ━━━" -ForegroundColor Green
  az containerapp update --name $info.app --resource-group $ResourceGroup --image $imageFull --output none
  if ($LASTEXITCODE -ne 0) { Write-Error "Deploy failed for $svc"; continue }
  
  Write-Host "✅ $svc deployed: $imageFull" -ForegroundColor Green
}

Write-Host "`n🏁 Deployment complete (tag: $tag)" -ForegroundColor Yellow
