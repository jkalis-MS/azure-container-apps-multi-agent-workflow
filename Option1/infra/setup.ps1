#!/usr/bin/env pwsh
<#
.SYNOPSIS
  First-time setup: provisions all Azure resources for the World Cup Simulator demo.
.DESCRIPTION
  Creates resource group, ACR, Azure OpenAI, deploys Bicep (environments + apps + sandbox group),
  assigns RBAC for the simulator's Managed Identity, and creates the sandbox secret.

  After this script completes, the demo is fully operational.

  For subsequent code changes, use deploy.ps1 to rebuild and redeploy.
.PARAMETER Location
  Azure region for all resources. Default: westcentralus
.PARAMETER OpenAILocation
  Azure region for OpenAI (some models aren't available in all regions). Default: westus3
.EXAMPLE
  # Interactive — prompts for values not set in environment:
  ./infra/setup.ps1

  # Non-interactive — all values from .env:
  Get-Content .env | ForEach-Object { if ($_ -match '^([^#=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($Matches[1].Trim(), $Matches[2].Trim()) } }
  ./infra/setup.ps1
#>
param(
  [string]$Location = "westcentralus",
  [string]$OpenAILocation = "westus3"
)

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Auto-load .env file if present (from repo root)
# ---------------------------------------------------------------------------
$envFile = Join-Path $PSScriptRoot "..\.env"
if (Test-Path $envFile) {
  Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*([^#=]+?)\s*=\s*(.*)\s*$') {
      [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2])
    }
  }
  Write-Host "  Loaded .env file" -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# Helper: read from env or prompt
# ---------------------------------------------------------------------------
function Get-Param {
  param([string]$EnvVar, [string]$Prompt, [switch]$Secure)
  $val = [Environment]::GetEnvironmentVariable($EnvVar)
  if ($val) { return $val }
  if ($Secure) {
    $secStr = Read-Host -Prompt $Prompt -AsSecureString
    return [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secStr))
  }
  return Read-Host -Prompt $Prompt
}

Write-Host "`n⚽ World Cup Simulator — First-Time Setup" -ForegroundColor Cyan
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# Gather parameters
# ---------------------------------------------------------------------------
$subscriptionId = Get-Param -EnvVar "AZURE_SUBSCRIPTION_ID" -Prompt "Azure Subscription ID"
$resourceGroup  = Get-Param -EnvVar "ACA_RESOURCE_GROUP" -Prompt "Resource Group name"
$acrName        = Get-Param -EnvVar "ACR_NAME" -Prompt "Container Registry name (globally unique)"
$sandboxGroup   = Get-Param -EnvVar "ACA_SANDBOX_GROUP" -Prompt "Sandbox Group name"
$openaiEndpoint = Get-Param -EnvVar "AZURE_OPENAI_ENDPOINT" -Prompt "Azure OpenAI endpoint URL"

# Get OpenAI key: prefer .env / environment variable, fall back to fetching from resource
$openaiKey = [Environment]::GetEnvironmentVariable("AZURE_OPENAI_KEY")
if ($openaiKey) {
  Write-Host "  Using OpenAI key from environment variable" -ForegroundColor DarkGray
} else {
  $openaiResourceName = ([Uri]$openaiEndpoint).Host.Split('.')[0]
  $openaiResourceGroup = if ($env:AZURE_OPENAI_RESOURCE_GROUP) { $env:AZURE_OPENAI_RESOURCE_GROUP } else { $resourceGroup }
  Write-Host "  Fetching OpenAI key from resource '$openaiResourceName' (RG: $openaiResourceGroup)..." -ForegroundColor DarkGray
  $openaiKey = az cognitiveservices account keys list `
    --name $openaiResourceName `
    --resource-group $openaiResourceGroup `
    --query "key1" -o tsv 2>&1
  if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to fetch OpenAI key. Set AZURE_OPENAI_KEY in .env or ensure resource '$openaiResourceName' exists in RG '$openaiResourceGroup'."
    exit 1
  }
}

Write-Host "`nConfiguration:" -ForegroundColor Yellow
Write-Host "  Subscription:  $subscriptionId"
Write-Host "  Resource Group: $resourceGroup"
Write-Host "  ACR:           $acrName"
Write-Host "  Sandbox Group: $sandboxGroup"
Write-Host "  Location:      $Location"
Write-Host "  OpenAI:        key configured ✓"
Write-Host ""

# Set subscription
az account set --subscription $subscriptionId

# ---------------------------------------------------------------------------
# Step 1: Resource Group
# ---------------------------------------------------------------------------
Write-Host "━━━ Step 1/6: Resource Group ━━━" -ForegroundColor Cyan
$rgExists = az group exists --name $resourceGroup -o tsv
if ($rgExists -eq "true") {
  Write-Host "  Already exists ✓" -ForegroundColor DarkGray
} else {
  az group create --name $resourceGroup --location $Location --output none
  Write-Host "  Created ✓" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Step 2: Container Registry
# ---------------------------------------------------------------------------
Write-Host "━━━ Step 2/6: Container Registry ━━━" -ForegroundColor Cyan
$acrExists = az acr show --name $acrName --query "name" -o tsv 2>$null
if ($acrExists) {
  Write-Host "  Already exists ✓" -ForegroundColor DarkGray
} else {
  az acr create --name $acrName --resource-group $resourceGroup --sku Basic --admin-enabled true --output none
  Write-Host "  Created ✓" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Step 3: Build Container Images
# ---------------------------------------------------------------------------
Write-Host "━━━ Step 3/6: Building Container Images ━━━" -ForegroundColor Cyan
$images = @(
  @{ name = "wc-orchestrator"; path = "packages/orchestrator" }
  @{ name = "wc-simulator-mi"; path = "packages/simulator" }
  @{ name = "wc-blog-gen"; path = "packages/blog-generator" }
  @{ name = "wc-narration-gen"; path = "packages/narration-generator" }
)
foreach ($img in $images) {
  Write-Host "  Building $($img.name)..."
  az acr build --registry $acrName --image "$($img.name):latest" $img.path --no-logs --output none
  if ($LASTEXITCODE -ne 0) { Write-Error "Build failed for $($img.name)" }
  Write-Host "    ✓ $($img.name):latest" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Step 4: Create Sandbox Group (via REST — preview API)
# ---------------------------------------------------------------------------
Write-Host "━━━ Step 4/6: Sandbox Group ━━━" -ForegroundColor Cyan

# Sandbox group (preview API not in Bicep types)
$sbGroupUrl = "/subscriptions/$subscriptionId/resourceGroups/$resourceGroup/providers/Microsoft.App/sandboxGroups/$sandboxGroup`?api-version=2026-02-01-preview"
$sbBody = @{location=$Location} | ConvertTo-Json -Compress
$sbBody | Out-File -FilePath "$env:TEMP\sb-group.json" -Encoding utf8
try {
  az rest --method PUT --url $sbGroupUrl --body "@$env:TEMP\sb-group.json" --output none 2>$null
  Write-Host "  Sandbox group created ✓" -ForegroundColor Green
} catch {
  Write-Host "  Sandbox group already exists ✓" -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# Step 5: Deploy Bicep (standard env + 4 Container Apps)
# ---------------------------------------------------------------------------
Write-Host "━━━ Step 5/6: Deploying Infrastructure (Bicep) ━━━" -ForegroundColor Cyan
$acrPassword = az acr credential show --name $acrName --query "passwords[0].value" -o tsv
$acrServer = "$acrName.azurecr.io"

$deployment = az deployment group create `
  --resource-group $resourceGroup `
  --template-file infra/main.bicep `
  --parameters `
    acrServer=$acrServer `
    acrUsername=$acrName `
    acrPassword=$acrPassword `
    openaiEndpoint=$openaiEndpoint `
    openaiKey=$openaiKey `
    sandboxGroup=$sandboxGroup `
  --query "properties.outputs" `
  -o json | ConvertFrom-Json

if ($LASTEXITCODE -ne 0) { Write-Error "Bicep deployment failed" }

$orchestratorUrl = $deployment.orchestratorUrl.value
$simulatorUrl = $deployment.simulatorUrl.value
Write-Host "  Orchestrator: $orchestratorUrl" -ForegroundColor Green
Write-Host "  Simulator:    $simulatorUrl" -ForegroundColor Green
Write-Host "  Blog Gen:     $($deployment.blogGenUrl.value)" -ForegroundColor Green
Write-Host "  Narration:    $($deployment.narrationGenUrl.value)" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Step 6/6: Assign RBAC for Simulator Managed Identity → Sandbox Group
# ---------------------------------------------------------------------------
Write-Host "━━━ Step 6/6: RBAC for Simulator MI ━━━" -ForegroundColor Cyan
$principalId = $deployment.simulatorPrincipalId.value
$sbScope = "/subscriptions/$subscriptionId/resourceGroups/$resourceGroup/providers/Microsoft.App/sandboxGroups/$sandboxGroup"

try {
  $null = az role assignment create `
    --assignee $principalId `
    --role Contributor `
    --scope $sbScope `
    --output none 2>&1
  Write-Host "  Assigned Contributor on sandbox group ✓" -ForegroundColor Green
} catch {
  Write-Host "  Contributor role already exists ✓" -ForegroundColor DarkGray
}

try {
  $null = az role assignment create `
    --assignee $principalId `
    --role "Container Apps SandboxGroup Data Owner" `
    --scope $sbScope `
    --output none 2>&1
  Write-Host "  Assigned SandboxGroup Data Owner ✓" -ForegroundColor Green
} catch {
  Write-Host "  SandboxGroup Data Owner role already exists ✓" -ForegroundColor DarkGray
}

# Note: Sandbox group secret (aoai-api-key) is created at runtime by the simulator
# via the SDK data plane — no ARM-level provisioning needed.

# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
Write-Host "━━━ Verification ━━━" -ForegroundColor Cyan
Write-Host "  Waiting 30s for apps to start..." -ForegroundColor DarkGray
Start-Sleep -Seconds 30

try {
  $health = Invoke-RestMethod -Uri "$orchestratorUrl/health" -TimeoutSec 10
  Write-Host "  Orchestrator health: $($health.status) ✓" -ForegroundColor Green
} catch {
  Write-Host "  ⚠️  Orchestrator not responding yet (may need more time to cold-start)" -ForegroundColor Yellow
}

try {
  $health = Invoke-RestMethod -Uri "$simulatorUrl/health" -TimeoutSec 10
  Write-Host "  Simulator health: $($health.status) ✓" -ForegroundColor Green
} catch {
  Write-Host "  ⚠️  Simulator not responding yet (may need more time to cold-start)" -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# Done!
# ---------------------------------------------------------------------------
Write-Host "`n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
Write-Host "🏁 Setup complete!" -ForegroundColor Green
Write-Host ""
Write-Host "  Open the demo: $orchestratorUrl" -ForegroundColor Yellow
Write-Host ""
Write-Host "  For subsequent deployments, use:" -ForegroundColor DarkGray
Write-Host "    ./infra/deploy.ps1" -ForegroundColor DarkGray
Write-Host ""
