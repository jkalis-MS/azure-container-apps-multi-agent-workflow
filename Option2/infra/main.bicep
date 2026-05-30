targetScope = 'resourceGroup'

@description('Azure environment name (set automatically by azd)')
param environmentName string

@description('Base name for all resources')
param baseName string = environmentName

@description('Location for all resources')
param location string = resourceGroup().location

@description('Azure OpenAI endpoint (optional -- defaults to Foundry endpoint)')
param azureOpenAiEndpoint string = ''

@description('Azure OpenAI API key (optional -- defaults to Foundry key)')
@secure()
param azureOpenAiApiKey string = ''

@description('Azure OpenAI deployment name')
param azureOpenAiDeployment string = 'gpt-4o'

@description('ACA Sandbox Group name used by Agent 1 for runtime sandbox creation')
param sandboxGroupName string = '${baseName}-sg-research'

@description('User-assigned identity name for ACA Sandbox Group')
param sandboxGroupUamiName string = '${baseName}-sg-uami'

// Container image names -- azd sets these during deploy; placeholder used for initial provision
var defaultImage = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
param agentResearchImageName string = ''
param agentCreatorImageName string = ''
param devUiImageName string = ''
param agentPodcasterImageName string = ''
param ttsServerImageName string = ''
param ttsModelPullImageName string = ''

@description('Content factory mode: lab (Azure OpenAI TTS) or full (GPU XTTS-v2)')
param contentFactoryMode string = 'lab'

@description('Azure Storage connection string for podcast audio uploads')
@secure()
param azureStorageConnectionString string = ''

// Deterministic A2A auth token (GUID per deployment)
var a2aToken = guid(resourceGroup().id, baseName, 'a2a-token')

// Built-in role definition IDs
var roleContributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b24988ac-6180-42a0-ab88-20f7382dd24c')
var roleAcrPull = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
var roleSandboxGroupDataOwner = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'c24cf47c-5077-412d-a19c-45202126392c')

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

// GPT-4o model deployment inside Foundry
resource gptDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: aiServices
  name: azureOpenAiDeployment
  sku: {
    name: 'GlobalStandard'
    capacity: 1
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

// VNet for NFS private endpoint connectivity (full mode only)
resource vnet 'Microsoft.Network/virtualNetworks@2023-06-01' = if (contentFactoryMode == 'full') {
  name: '${baseName}-vnet'
  location: location
  properties: {
    addressSpace: { addressPrefixes: ['10.0.0.0/16'] }
    subnets: [
      {
        name: 'infrastructure'
        properties: {
          addressPrefix: '10.0.0.0/23'
          delegations: [
            {
              name: 'aca-delegation'
              properties: { serviceName: 'Microsoft.App/environments' }
            }
          ]
        }
      }
      {
        name: 'private-endpoints'
        properties: {
          addressPrefix: '10.0.2.0/24'
        }
      }
    ]
  }
}

// Container Apps Environment with OTEL collector -> App Insights
resource acaEnv 'Microsoft.App/managedEnvironments@2024-10-02-preview' = {
  name: '${baseName}-env'
  location: location
  properties: {
    vnetConfiguration: contentFactoryMode == 'full' ? {
      infrastructureSubnetId: vnet.properties.subnets[0].id
      internal: false
    } : null
    workloadProfiles: contentFactoryMode == 'full' ? [
      { name: 'Consumption', workloadProfileType: 'Consumption' }
      { name: 'gpu-t4', workloadProfileType: 'Consumption-GPU-NC8as-T4' }
    ] : [
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

// Azure Container Registry
resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: replace('${baseName}acr', '-', '')
  location: location
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: true }
}

resource sandboxGroupUami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: sandboxGroupUamiName
  location: location
}

// ACA Sandbox Group used by agent-research at runtime
resource sandboxGroup 'Microsoft.App/sandboxGroups@2026-02-01-preview' = {
  name: sandboxGroupName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${sandboxGroupUami.id}': {}
    }
  }
  properties: {
    imageRegistryCredentials: [
      {
        server: acr.properties.loginServer
        identity: sandboxGroupUami.id
      }
    ]
  }
}

// Sandbox group identity can pull images from ACR
resource sandboxGroupAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, sandboxGroupUami.id, 'sandbox-group-acr-pull')
  scope: acr
  properties: {
    principalId: sandboxGroupUami.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: roleAcrPull
  }
}

// Additional access often needed by sandbox group MI for runtime operations
resource sandboxGroupMiContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(sandboxGroup.id, sandboxGroupUami.id, 'sandbox-group-mi-contributor')
  scope: sandboxGroup
  properties: {
    principalId: sandboxGroupUami.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: roleContributor
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

// Premium NFS FileStorage for XTTS-v2 model persistence (full mode only)
resource nfsStorage 'Microsoft.Storage/storageAccounts@2023-01-01' = if (contentFactoryMode == 'full') {
  name: replace('${baseName}nfs', '-', '')
  location: location
  sku: { name: 'Premium_LRS' }
  kind: 'FileStorage'
  properties: {
    allowBlobPublicAccess: false
    publicNetworkAccess: 'Disabled'
    supportsHttpsTrafficOnly: false
    allowSharedKeyAccess: true
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Deny'
    }
  }
}

resource nfsFileService 'Microsoft.Storage/storageAccounts/fileServices@2023-01-01' = if (contentFactoryMode == 'full') {
  parent: nfsStorage
  name: 'default'
}

resource nfsShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-01-01' = if (contentFactoryMode == 'full') {
  parent: nfsFileService
  name: 'xtts-models'
  properties: {
    enabledProtocols: 'NFS'
    shareQuota: 100
    rootSquash: 'NoRootSquash'
  }
}

// Private endpoint for NFS storage (full mode only)
resource nfsPrivateEndpoint 'Microsoft.Network/privateEndpoints@2023-06-01' = if (contentFactoryMode == 'full') {
  name: '${baseName}-nfs-pe'
  location: location
  properties: {
    subnet: { id: vnet.properties.subnets[1].id }
    privateLinkServiceConnections: [
      {
        name: 'nfs-connection'
        properties: {
          privateLinkServiceId: nfsStorage.id
          groupIds: ['file']
        }
      }
    ]
  }
}

// Private DNS zone for NFS resolution (full mode only)
resource nfsDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = if (contentFactoryMode == 'full') {
  name: 'privatelink.file.core.windows.net'
  location: 'global'
}

resource nfsDnsZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = if (contentFactoryMode == 'full') {
  parent: nfsDnsZone
  name: 'vnet-link'
  location: 'global'
  properties: {
    virtualNetwork: { id: vnet.id }
    registrationEnabled: false
  }
}

resource nfsDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-06-01' = if (contentFactoryMode == 'full') {
  parent: nfsPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'config'
        properties: {
          privateDnsZoneId: nfsDnsZone.id
        }
      }
    ]
  }
}

// NFS storage mount on ACA environment (full mode only)
resource xttsModelStorage 'Microsoft.App/managedEnvironments/storages@2024-10-02-preview' = if (contentFactoryMode == 'full') {
  parent: acaEnv
  name: 'xtts-model-storage'
  dependsOn: [nfsDnsZoneGroup, nfsDnsZoneLink]
  properties: {
    nfsAzureFile: {
      server: '${nfsStorage.name}.file.core.windows.net'
      shareName: '/${nfsStorage.name}/xtts-models'
      accessMode: 'ReadWrite'
    }
  }
}

// Agent 1 - Research (Python/LangGraph)
resource agentResearch 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${baseName}-agent-research'
  location: location
  tags: { 'azd-service-name': 'agent-research' }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8001
        transport: 'http'
      }
      registries: [
        {
          server: acr.properties.loginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        { name: 'acr-password', value: acr.listCredentials().passwords[0].value }
        { name: 'azure-openai-key', value: resolvedOpenAiApiKey }
        { name: 'a2a-auth-token', value: a2aToken }
      ]
    }
    template: {
      containers: [
        {
          name: 'agent-research'
          image: agentResearchImageName != '' ? agentResearchImageName : defaultImage
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'AZURE_OPENAI_ENDPOINT', value: resolvedOpenAiEndpoint }
            { name: 'AZURE_OPENAI_API_KEY', secretRef: 'azure-openai-key' }
            { name: 'AZURE_OPENAI_DEPLOYMENT', value: azureOpenAiDeployment }
            { name: 'AZURE_OPENAI_API_VERSION', value: '2024-12-01-preview' }
            { name: 'CONTENT_FACTORY_MODE', value: 'lab' }
            { name: 'OTEL_SERVICE_NAME', value: 'research-agent' }
            // Capture gen_ai prompt/response content for the App Insights Agents blade
            { name: 'OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT', value: 'true' }
            // OTEL_EXPORTER_OTLP_ENDPOINT is auto-injected by ACA managed OTEL agent
            { name: 'A2A_AUTH_ENABLED', value: 'true' }
            { name: 'A2A_AUTH_TOKEN', secretRef: 'a2a-auth-token' }
            { name: 'AZURE_SUBSCRIPTION_ID', value: subscription().subscriptionId }
            { name: 'ACA_SANDBOX_RESOURCE_GROUP', value: resourceGroup().name }
            { name: 'ACA_SANDBOX_GROUP_NAME', value: sandboxGroup.name }
            { name: 'ACA_SANDBOXGROUP_REGION', value: location }
          ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 3 }
    }
  }
}

// Agent 1 can create sandboxes in the sandbox group
resource agentResearchSandboxGroupDataOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(sandboxGroup.id, agentResearch.id, 'research-sandboxgroup-data-owner')
  scope: sandboxGroup
  properties: {
    principalId: agentResearch.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: roleSandboxGroupDataOwner
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
      registries: [
        {
          server: acr.properties.loginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        { name: 'acr-password', value: acr.listCredentials().passwords[0].value }
        { name: 'azure-openai-key', value: resolvedOpenAiApiKey }
        { name: 'a2a-auth-token', value: a2aToken }
      ]
    }
    template: {
      containers: [
        {
          name: 'agent-creator'
          image: agentCreatorImageName != '' ? agentCreatorImageName : defaultImage
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'AZURE_OPENAI_ENDPOINT', value: resolvedOpenAiEndpoint }
            { name: 'AZURE_OPENAI_API_KEY', secretRef: 'azure-openai-key' }
            { name: 'AZURE_OPENAI_DEPLOYMENT', value: azureOpenAiDeployment }
            { name: 'CONTENT_FACTORY_MODE', value: 'lab' }
            { name: 'OTEL_SERVICE_NAME', value: 'creator-agent' }
            // Capture gen_ai prompt/response content for the App Insights Agents blade
            { name: 'OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT', value: 'true' }
            // OTEL_EXPORTER_OTLP_ENDPOINT is auto-injected by ACA managed OTEL agent
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
      registries: [
        {
          server: acr.properties.loginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        { name: 'acr-password', value: acr.listCredentials().passwords[0].value }
        { name: 'azure-openai-key', value: resolvedOpenAiApiKey }
        { name: 'azure-storage-connection', value: azureStorageConnectionString != '' ? azureStorageConnectionString : 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value};EndpointSuffix=core.windows.net' }
        { name: 'a2a-auth-token', value: a2aToken }
      ]
    }
    template: {
      containers: [
        {
          name: 'agent-podcaster'
          image: agentPodcasterImageName != '' ? agentPodcasterImageName : defaultImage
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'AZURE_OPENAI_ENDPOINT', value: resolvedOpenAiEndpoint }
            { name: 'AZURE_OPENAI_API_KEY', secretRef: 'azure-openai-key' }
            { name: 'AZURE_OPENAI_DEPLOYMENT', value: azureOpenAiDeployment }
            { name: 'AZURE_OPENAI_API_VERSION', value: '2024-12-01-preview' }
            { name: 'CONTENT_FACTORY_MODE', value: contentFactoryMode }
            { name: 'TTS_SERVER_URL', value: contentFactoryMode == 'full' ? 'https://${ttsServer.properties.configuration.ingress.fqdn}' : '' }
            { name: 'AZURE_STORAGE_CONNECTION_STRING', secretRef: 'azure-storage-connection' }
            { name: 'AGENT_BASE_URL', value: 'https://${baseName}-agent-podcaster.${acaEnv.properties.defaultDomain}' }
            { name: 'OTEL_SERVICE_NAME', value: 'podcaster-agent' }
            // Capture gen_ai prompt/response content for the App Insights Agents blade
            { name: 'OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT', value: 'true' }
            // OTEL_EXPORTER_OTLP_ENDPOINT is auto-injected by ACA managed OTEL agent
            { name: 'A2A_AUTH_ENABLED', value: 'true' }
            { name: 'A2A_AUTH_TOKEN', secretRef: 'a2a-auth-token' }
          ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}

// TTS Server (GPU XTTS-v2) - only deployed in 'full' mode
resource ttsServer 'Microsoft.App/containerApps@2024-10-02-preview' = if (contentFactoryMode == 'full') {
  name: '${baseName}-tts-server'
  location: location
  tags: { 'azd-service-name': 'tts-server' }
  properties: {
    managedEnvironmentId: acaEnv.id
    workloadProfileName: 'gpu-t4'
    configuration: {
      ingress: {
        external: false
        targetPort: 8004
        transport: 'http'
      }
      registries: [
        {
          server: acr.properties.loginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        { name: 'acr-password', value: acr.listCredentials().passwords[0].value }
      ]
    }
    template: {
      initContainers: [
        {
          name: 'model-prefetch'
          image: ttsModelPullImageName != '' ? ttsModelPullImageName : (ttsServerImageName != '' ? ttsServerImageName : defaultImage)
          command: ['sh', '-c', 'command -v huggingface-cli >/dev/null 2>&1 && { test -f /models/xtts-v2/config.json && echo "XTTS-v2 model cached on NFS" || { echo "Downloading XTTS-v2..." && huggingface-cli download coqui/XTTS-v2 --local-dir /models/xtts-v2 && echo "Download complete"; }; } || echo "Skipping model pull (placeholder image)"']
          resources: { cpu: json('2'), memory: '4Gi' }
          volumeMounts: [
            { volumeName: 'xtts-models', mountPath: '/models' }
          ]
        }
      ]
      containers: [
        {
          name: 'tts-server'
          image: ttsServerImageName != '' ? ttsServerImageName : defaultImage
          resources: { cpu: json('8'), memory: '28Gi' }
          env: [
            { name: 'MODEL_PATH', value: '/models/xtts-v2' }
            { name: 'COQUI_TOS_AGREED', value: '1' }
            { name: 'OTEL_SERVICE_NAME', value: 'tts-server' }
            { name: 'OTEL_EXPORTER_OTLP_ENDPOINT', value: 'http://localhost:4317' }
          ]
          volumeMounts: [
            { volumeName: 'xtts-models', mountPath: '/models' }
          ]
          probes: [
            {
              type: 'Startup'
              httpGet: { path: '/health', port: 8004 }
              initialDelaySeconds: 60
              periodSeconds: 30
              failureThreshold: 20
            }
            {
              type: 'Readiness'
              httpGet: { path: '/health', port: 8004 }
              periodSeconds: 15
            }
          ]
        }
      ]
      volumes: [
        {
          name: 'xtts-models'
          storageType: 'NfsAzureFile'
          storageName: xttsModelStorage.name
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
      registries: [
        {
          server: acr.properties.loginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        { name: 'acr-password', value: acr.listCredentials().passwords[0].value }
        { name: 'a2a-auth-token', value: a2aToken }
      ]
    }
    template: {
      containers: [
        {
          name: 'dev-ui'
          image: devUiImageName != '' ? devUiImageName : defaultImage
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

output acrLoginServer string = acr.properties.loginServer
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = acr.properties.loginServer
output agentResearchUrl string = 'https://${agentResearch.properties.configuration.ingress.fqdn}'
output agentCreatorUrl string = 'https://${agentCreator.properties.configuration.ingress.fqdn}'
output devUiUrl string = 'https://${devUi.properties.configuration.ingress.fqdn}'
output appInsightsConnectionString string = appInsights.properties.ConnectionString
output appInsightsName string = appInsights.name
output foundryEndpoint string = aiServices.properties.endpoint
output foundryProjectName string = foundryProject.name
output sandboxGroupResourceId string = sandboxGroup.id
output sandboxGroupIdentityPrincipalId string = sandboxGroupUami.properties.principalId
output agentResearchOtelId string = 'research-agent'
output agentCreatorOtelId string = 'creator-agent'
output agentResearchA2ACard string = 'https://${agentResearch.properties.configuration.ingress.fqdn}/.well-known/agent.json'
output agentCreatorA2ACard string = 'https://${agentCreator.properties.configuration.ingress.fqdn}/.well-known/agent.json'
output agentPodcasterUrl string = 'https://${agentPodcaster.properties.configuration.ingress.fqdn}'
output agentPodcasterA2ACard string = 'https://${agentPodcaster.properties.configuration.ingress.fqdn}/.well-known/agent.json'
output storageAccountName string = storageAccount.name
output storageConnectionString string = 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'
