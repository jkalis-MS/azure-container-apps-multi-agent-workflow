// Quick Lab Deploy — standalone Bicep for lab vendor provisioning
// Generated from main.bicep (lab mode only, pre-built images from acateam.azurecr.io)
//
// Usage:
//   az group create -n rg-mvp-lab -l westus3
//   az deployment group create -g rg-mvp-lab -f bulk-lab-deploy.bicep

targetScope = 'resourceGroup'

@description('Lab ID (8 digits, e.g., 12341234)')
@minLength(8)
@maxLength(8)
param labInstanceId string 

@description('Environment name based on Skillable Lab ID')
param environmentName string = 'aca${labInstanceId}'

@description('Base name for all resources')
param baseName string = environmentName

@description('Location for all resources')
param location string = 'westus3'

@description('Azure OpenAI endpoint (optional -- defaults to Foundry endpoint)')
param azureOpenAiEndpoint string = ''

@description('Azure OpenAI API key (optional -- defaults to Foundry key)')
@secure()
param azureOpenAiApiKey string = ''

@description('Azure OpenAI deployment name')
param azureOpenAiDeployment string = 'gpt-4o'

// Hardcoded container images (anonymous pull from acateam.azurecr.io)
var agentResearchImage = 'acateam.azurecr.io/2026-mvp-lab/agent-research:latest'
var agentCreatorImage = 'acateam.azurecr.io/2026-mvp-lab/agent-creator:latest'
var agentPodcasterImage = 'acateam.azurecr.io/2026-mvp-lab/agent-podcaster:latest'
var devUiImage = 'acateam.azurecr.io/2026-mvp-lab/dev-ui:latest'

// Deterministic A2A auth token (GUID per deployment)
var a2aToken = guid(resourceGroup().id, baseName, 'a2a-token')

// Resolved values: use provided overrides or fall back to Foundry resource
var resolvedOpenAiEndpoint = azureOpenAiEndpoint != '' ? azureOpenAiEndpoint : aiServices.properties.endpoint
var resolvedOpenAiApiKey = azureOpenAiApiKey != '' ? azureOpenAiApiKey : aiServices.listKeys().key1

// Log Analytics Workspace
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${baseName}-logs'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

// Application Insights (telemetry destination for OTEL collector)
resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${baseName}-appinsights'
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

// Azure AI Services (Foundry) account
resource aiServices 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: '${baseName}-foundry'
  location: location
  kind: 'AIServices'
  identity: {
    type: 'SystemAssigned'
  }
  sku: { name: 'S0' }
  properties: {
    publicNetworkAccess: 'Enabled'
    customSubDomainName: '${baseName}-foundry'
    allowProjectManagement: true
  }
}

// Foundry project
resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: aiServices
  name: '${baseName}-project'
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {}
}

// GPT-5 model deployment inside Foundry
resource gptDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: aiServices
  name: azureOpenAiDeployment
  sku: {
    name: 'GlobalStandard'
    capacity: 75
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4o'
      version: '2024-11-20'
    }
  }
}

// TTS model deployment for podcaster audio synthesis
resource ttsDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: aiServices
  name: 'tts-1'
  dependsOn: [gptDeployment]
  sku: {
    name: 'Standard'
    capacity: 1
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'tts'
      version: '001'
    }
  }
}

// Container Apps Environment with OTEL collector -> App Insights
resource acaEnv 'Microsoft.App/managedEnvironments@2024-10-02-preview' = {
  name: '${baseName}-env'
  location: location
  properties: {
    workloadProfiles: [
      { name: 'Consumption', workloadProfileType: 'Consumption' }
    ]
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
    appInsightsConfiguration: {
      connectionString: appInsights.properties.ConnectionString
    }
    openTelemetryConfiguration: {
      destinationsConfiguration: {}
      tracesConfiguration: {
        destinations: ['appInsights']
      }
      logsConfiguration: {
        destinations: ['appInsights']
      }
      metricsConfiguration: {
        destinations: ['appInsights']
      }
    }
  }
}

// Storage Account for podcast audio files
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: replace('${baseName}store', '-', '')
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-01-01' = {
  parent: storageAccount
  name: 'default'
}

resource podcastContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobService
  name: 'podcasts'
  properties: {
    publicAccess: 'None'
  }
}

// Agent 1 - Research (Python/LangGraph)
resource agentResearch 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${baseName}-agent-research'
  location: location
  tags: { 'azd-service-name': 'agent-research' }
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8001
        transport: 'http'
      }
      secrets: [
        { name: 'azure-openai-key', value: resolvedOpenAiApiKey }
        { name: 'a2a-auth-token', value: a2aToken }
      ]
    }
    template: {
      containers: [
        {
          name: 'agent-research'
          image: agentResearchImage
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'AZURE_OPENAI_ENDPOINT', value: resolvedOpenAiEndpoint }
            { name: 'AZURE_OPENAI_API_KEY', secretRef: 'azure-openai-key' }
            { name: 'AZURE_OPENAI_DEPLOYMENT', value: azureOpenAiDeployment }
            { name: 'AZURE_OPENAI_API_VERSION', value: '2024-12-01-preview' }
            { name: 'CONTENT_FACTORY_MODE', value: 'lab' }
            { name: 'OTEL_SERVICE_NAME', value: 'research-agent' }
            { name: 'OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT', value: 'true' }
            { name: 'A2A_AUTH_ENABLED', value: 'true' }
            { name: 'A2A_AUTH_TOKEN', secretRef: 'a2a-auth-token' }
          ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 3 }
    }
  }
}

// Agent 2 - Content Creator (.NET)
resource agentCreator 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${baseName}-agent-creator'
  location: location
  tags: { 'azd-service-name': 'agent-creator' }
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8002
        transport: 'http'
      }
      secrets: [
        { name: 'azure-openai-key', value: resolvedOpenAiApiKey }
        { name: 'a2a-auth-token', value: a2aToken }
      ]
    }
    template: {
      containers: [
        {
          name: 'agent-creator'
          image: agentCreatorImage
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'AZURE_OPENAI_ENDPOINT', value: resolvedOpenAiEndpoint }
            { name: 'AZURE_OPENAI_API_KEY', secretRef: 'azure-openai-key' }
            { name: 'AZURE_OPENAI_DEPLOYMENT', value: azureOpenAiDeployment }
            { name: 'CONTENT_FACTORY_MODE', value: 'lab' }
            { name: 'OTEL_SERVICE_NAME', value: 'creator-agent' }
            { name: 'OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT', value: 'true' }
            { name: 'A2A_AUTH_ENABLED', value: 'true' }
            { name: 'A2A_AUTH_TOKEN', secretRef: 'a2a-auth-token' }
          ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}

// Agent 3 - Podcaster (Python/FastAPI)
resource agentPodcaster 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${baseName}-agent-podcaster'
  location: location
  tags: { 'azd-service-name': 'agent-podcaster' }
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8003
        transport: 'http'
      }
      secrets: [
        { name: 'azure-openai-key', value: resolvedOpenAiApiKey }
        { name: 'azure-storage-connection', value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value};EndpointSuffix=core.windows.net' }
        { name: 'a2a-auth-token', value: a2aToken }
      ]
    }
    template: {
      containers: [
        {
          name: 'agent-podcaster'
          image: agentPodcasterImage
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'AZURE_OPENAI_ENDPOINT', value: resolvedOpenAiEndpoint }
            { name: 'AZURE_OPENAI_API_KEY', secretRef: 'azure-openai-key' }
            { name: 'AZURE_OPENAI_DEPLOYMENT', value: azureOpenAiDeployment }
            { name: 'AZURE_OPENAI_API_VERSION', value: '2024-12-01-preview' }
            { name: 'CONTENT_FACTORY_MODE', value: 'lab' }
            { name: 'TTS_SERVER_URL', value: '' }
            { name: 'AZURE_STORAGE_CONNECTION_STRING', secretRef: 'azure-storage-connection' }
            { name: 'AGENT_BASE_URL', value: 'https://${baseName}-agent-podcaster.${acaEnv.properties.defaultDomain}' }
            { name: 'OTEL_SERVICE_NAME', value: 'podcaster-agent' }
            { name: 'OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT', value: 'true' }
            { name: 'A2A_AUTH_ENABLED', value: 'true' }
            { name: 'A2A_AUTH_TOKEN', secretRef: 'a2a-auth-token' }
          ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}

// Dev UI
resource devUi 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${baseName}-dev-ui'
  location: location
  dependsOn: [agentResearch, agentCreator, agentPodcaster]
  tags: { 'azd-service-name': 'dev-ui' }
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8080
        transport: 'http'
      }
      secrets: [
        { name: 'a2a-auth-token', value: a2aToken }
      ]
    }
    template: {
      containers: [
        {
          name: 'dev-ui'
          image: devUiImage
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
          env: [
            { name: 'AGENT1_URL', value: 'https://${agentResearch.properties.configuration.ingress.fqdn}' }
            { name: 'AGENT2_URL', value: 'https://${agentCreator.properties.configuration.ingress.fqdn}' }
            { name: 'AGENT3_URL', value: 'https://${agentPodcaster.properties.configuration.ingress.fqdn}' }
            { name: 'A2A_AUTH_TOKEN', secretRef: 'a2a-auth-token' }
          ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}

output agentResearchUrl string = 'https://${agentResearch.properties.configuration.ingress.fqdn}'
output agentCreatorUrl string = 'https://${agentCreator.properties.configuration.ingress.fqdn}'
output devUiUrl string = 'https://${devUi.properties.configuration.ingress.fqdn}'
output appInsightsConnectionString string = appInsights.properties.ConnectionString
output appInsightsName string = appInsights.name
output foundryEndpoint string = aiServices.properties.endpoint
output foundryProjectName string = foundryProject.name
output agentResearchOtelId string = 'research-agent'
output agentCreatorOtelId string = 'creator-agent'
output agentResearchA2ACard string = 'https://${agentResearch.properties.configuration.ingress.fqdn}/.well-known/agent.json'
output agentCreatorA2ACard string = 'https://${agentCreator.properties.configuration.ingress.fqdn}/.well-known/agent.json'
output agentPodcasterUrl string = 'https://${agentPodcaster.properties.configuration.ingress.fqdn}'
output agentPodcasterA2ACard string = 'https://${agentPodcaster.properties.configuration.ingress.fqdn}/.well-known/agent.json'
output storageAccountName string = storageAccount.name
output storageConnectionString string = 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'