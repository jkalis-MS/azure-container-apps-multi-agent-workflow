// World Cup Match Simulator — Azure Infrastructure
// Provisions:
//   - Log Analytics workspace + Application Insights
//   - Standard environment (cae-worldcup-demo)
//   - 4 Container Apps (all on Standard environment)
//
// Prerequisites (created by setup.ps1 before this template runs):
//   - Sandbox group (via REST — preview API not in Bicep types)

targetScope = 'resourceGroup'

@description('Location for all resources')
param location string = resourceGroup().location

@description('ACR server (e.g. myacr.azurecr.io)')
param acrServer string

@description('ACR admin username')
param acrUsername string

@secure()
@description('ACR admin password')
param acrPassword string

@description('Azure OpenAI endpoint')
param openaiEndpoint string

@secure()
@description('Azure OpenAI API key')
param openaiKey string

@description('Azure OpenAI deployment name')
param openaiDeployment string = 'gpt-4o'

@description('Azure OpenAI TTS deployment name')
param openaiTtsDeployment string = 'tts-hd'

@description('Sandbox group name')
param sandboxGroup string = 'sbg-worldcup-demo'

@description('Image tag to deploy')
param imageTag string = 'latest'

@description('Location for Application Insights (not available in all regions)')
param appInsightsLocation string = 'westus2'

// ---------------------------------------------------------------------------
// Observability — Log Analytics + Application Insights
// ---------------------------------------------------------------------------
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: 'law-worldcup-demo'
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-worldcup-demo'
  location: appInsightsLocation
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

// ---------------------------------------------------------------------------
// Workload Profiles environment — shared by all 4 apps
// ---------------------------------------------------------------------------
resource standardEnv 'Microsoft.App/managedEnvironments@2026-03-02-preview' = {
  name: 'cae-worldcup-demo'
  location: location
  properties: {
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
    openTelemetryConfiguration: {
      appInsightsConfiguration: {
        connectionString: appInsights.properties.ConnectionString
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Shared config
// ---------------------------------------------------------------------------

var registries = [
  {
    server: acrServer
    username: acrUsername
    passwordSecretRef: 'acr-password'
  }
]

// Standard env supports secretRef in env vars
var standardSecrets = [
  {
    name: 'acr-password'
    value: acrPassword
  }
  {
    name: 'openai-key'
    value: openaiKey
  }
]

// ---------------------------------------------------------------------------
// Orchestrator — Standard environment
// Node.js/Express: receives user prompt, calls all 3 agents, streams via SSE
// ---------------------------------------------------------------------------
resource orchestrator 'Microsoft.App/containerApps@2026-03-02-preview' = {
  name: 'wc-orchestrator'
  location: location
  properties: {
    workloadProfileName: 'Consumption'
    environmentId: standardEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 3000
      }
      registries: registries
      secrets: standardSecrets
    }
    template: {
      containers: [
        {
          name: 'main'
          image: '${acrServer}/wc-orchestrator:${imageTag}'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            {
              name: 'PORT'
              value: '3000'
            }
            {
              name: 'SIMULATOR_URL'
              value: 'https://${simulator.properties.configuration.ingress.fqdn}'
            }
            {
              name: 'BLOG_GEN_URL'
              value: 'https://${blogGen.properties.configuration.ingress.fqdn}'
            }
            {
              name: 'NARRATION_GEN_URL'
              value: 'https://${narrationGen.properties.configuration.ingress.fqdn}'
            }
            {
              name: 'OTEL_SERVICE_NAME'
              value: 'wc-orchestrator'
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsights.properties.ConnectionString
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Simulator — Standard environment with Managed Identity
// Python/FastAPI: LangGraph pipeline, AI code generation + sandbox execution
// ---------------------------------------------------------------------------
resource simulator 'Microsoft.App/containerApps@2026-03-02-preview' = {
  name: 'wc-simulator-mi'
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    workloadProfileName: 'Consumption'
    environmentId: standardEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 3001
      }
      registries: registries
      secrets: standardSecrets
    }
    template: {
      containers: [
        {
          name: 'main'
          image: '${acrServer}/wc-simulator-mi:${imageTag}'
          resources: {
            cpu: json('1')
            memory: '2Gi'
          }
          env: [
            {
              name: 'PORT'
              value: '3001'
            }
            {
              name: 'AZURE_OPENAI_ENDPOINT'
              value: openaiEndpoint
            }
            {
              name: 'AZURE_OPENAI_KEY'
              secretRef: 'openai-key'
            }
            {
              name: 'AZURE_OPENAI_DEPLOYMENT'
              value: openaiDeployment
            }
            {
              name: 'AZURE_SUBSCRIPTION_ID'
              value: subscription().subscriptionId
            }
            {
              name: 'ACA_RESOURCE_GROUP'
              value: resourceGroup().name
            }
            {
              name: 'ACA_SANDBOX_GROUP'
              value: sandboxGroup
            }
            {
              name: 'OTEL_SERVICE_NAME'
              value: 'wc-simulator'
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsights.properties.ConnectionString
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Blog Generator — Standard environment
// .NET/ASP.NET Core: takes simulation result, generates a match report article
// ---------------------------------------------------------------------------
resource blogGen 'Microsoft.App/containerApps@2026-03-02-preview' = {
  name: 'wc-blog-gen'
  location: location
  properties: {
    workloadProfileName: 'Consumption'
    environmentId: standardEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 3002
      }
      registries: registries
      secrets: standardSecrets
    }
    template: {
      containers: [
        {
          name: 'main'
          image: '${acrServer}/wc-blog-gen:${imageTag}'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            {
              name: 'ASPNETCORE_URLS'
              value: 'http://+:3002'
            }
            {
              name: 'Azure__OpenAI__Endpoint'
              value: openaiEndpoint
            }
            {
              name: 'Azure__OpenAI__Key'
              secretRef: 'openai-key'
            }
            {
              name: 'Azure__OpenAI__Deployment'
              value: openaiDeployment
            }
            {
              name: 'OTEL_SERVICE_NAME'
              value: 'wc-blog-gen'
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsights.properties.ConnectionString
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Narration Generator — Standard environment
// Python/FastAPI: generates sports commentary script + TTS-HD audio
// ---------------------------------------------------------------------------
resource narrationGen 'Microsoft.App/containerApps@2026-03-02-preview' = {
  name: 'wc-narration-gen'
  location: location
  properties: {
    workloadProfileName: 'Consumption'
    environmentId: standardEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 3003
      }
      registries: registries
      secrets: standardSecrets
    }
    template: {
      containers: [
        {
          name: 'main'
          image: '${acrServer}/wc-narration-gen:${imageTag}'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            {
              name: 'PORT'
              value: '3003'
            }
            {
              name: 'AZURE_OPENAI_ENDPOINT'
              value: openaiEndpoint
            }
            {
              name: 'AZURE_OPENAI_KEY'
              secretRef: 'openai-key'
            }
            {
              name: 'AZURE_OPENAI_DEPLOYMENT'
              value: openaiDeployment
            }
            {
              name: 'AZURE_OPENAI_TTS_DEPLOYMENT'
              value: openaiTtsDeployment
            }
            {
              name: 'OTEL_SERVICE_NAME'
              value: 'wc-narration-gen'
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsights.properties.ConnectionString
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
      }
    }
  }
}

// Outputs
output orchestratorUrl string = 'https://${orchestrator.properties.configuration.ingress.fqdn}'
output simulatorUrl string = 'https://${simulator.properties.configuration.ingress.fqdn}'
output blogGenUrl string = 'https://${blogGen.properties.configuration.ingress.fqdn}'
output narrationGenUrl string = 'https://${narrationGen.properties.configuration.ingress.fqdn}'
output simulatorPrincipalId string = simulator.identity.principalId
output appInsightsConnectionString string = appInsights.properties.ConnectionString
output logAnalyticsWorkspaceId string = logAnalytics.properties.customerId
