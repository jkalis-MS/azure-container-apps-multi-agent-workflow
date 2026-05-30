## About the lab...

A multi-agent content factory that research Microsoft technology topics, creates multi-format content, and optimizes output quality. The outcome is to familiarize with running and hosting AI Agents on Azure Container Apps, explore Agent observability and register and evaluate agents in Microsoft Foundry.

## Architecture
```
[Dev UI ] --> [Agent 1 - Researcher] --> [Agent 2 - Content Creator] --> [Agent 3 - Podcaster]
```
## What It Does

Enter a topic (e.g. "Write a comprehensive Blog post about Azure Container Apps for developers."). Three agents collaborate:

1. **Agent 1 -- Tech Research** (LangGraph / Python): Searches Microsoft Learn, Azure Blog, Tech Community, Azure Updates, and GitHub Azure-Samples. Uses AI for intent detection (extracting topic and target audience from the query), ranks sources by relevance, fetches content from the top hits, and synthesizes a debrief.
2. **Agent 2 -- Content Creator** (Microsoft Agent Framework / .NET): Transforms the research brief from Agent 1 into an original blog post, and social media posts -- all grounded in real sources.
3. **Agent 3 -- Podcaster** (GitHub Copilot CLI SDK / Python ): Creates an engaging podcast about the desired topic. It can either use a Text to Speech service from Microsoft Foundry (default) or a custom text-to-speech server running on serverless GPUs on Azure Container Apps.

Agents communicate via the A2A (Agent-to-Agent) protocol -- each exposes a /.well-known/agent.json card for discovery and a /a2a JSON-RPC endpoint for task submission. Each agent runs as a separate container on Azure Container Apps.

## Instructions
**In this lab, we first deploy the solution to Azure.**


### To get started 

There are many ways to run this solution - either run it locally or deploy to Azure. For Azure deployment you can use 'AZD UP' or follow steps 1-6 that are using pre-built container images.

1. Open the **Visual Studio Code**
2. Wait for the **Terminal** to initialize, or open it (Ctrl+`)
3.  **Clone this repo** by running 'git pull https://github.com/jkalis-MS/Content-Agent-Factory'
4. **Login to Azure portal** from the terminal 'az login', login in the browser, close the tab and get back to VS Code
5. In the VS Code Terminal **Create Azure resource group** for example 'az group create -n rg-mvp-lab -l westus3'
6. **Start the deployment** into the created resource grouop 'az deployment group create -g rg-mvp-lab -f infra/pre-rendered/bulk-lab-deploy.bicep'
    1. Yellow notifications are ok
    2. As your **labInstanceId**, enter any 8 random digits (to guarantee unique names of the resources)
7. Explore the project and architecture while the deployment is running (est. 6 minutes)

### To explore your deployment once done on **Azure portal**

We open the app we deployed on Container App and use the Agent Content Factory to generate our first content.

8. Open the **Azure portal**
16. Open the resource group, e.g. **rg-mvp-lab** 
17. Open the **aca<Lab_Instance_ID>-dev-ui** Container App
    1. Click on the **"Application Url"** on the top right corner in the Overview blade to open the Dev UI portal
18. Your "frontpage" to the Agentic Content Factory opens
    1. Wait until all 3 agents lights are green
19. Type your prompt - e.g. **"Azure Container Apps for developers"**
20. Start exploring results once they are available. You can also review the sample output in your repo - Lab\sample-output
21. Click **Copy DevUI Config**
    
### Observability with **Application Insights**

15.  Observability and **Application Insights**
    1. Open the **Application Insights** resource called **aca<Lab_Instance_ID>-appinsights** in your resource group **rg-mvp-lab**
    2. From the left navigation open **Investigate -> Agents (preview)** blade
    3. Explore all agent and tool calls and tokes use of your agents
    4. Note: *changing the **Time range** to last 15 or 30 minutes might provide a better view*
25. Don't forget to click **'Explore in Grafana'** for even more details including traces (all the way at the bottom)
    
### Register agents in **Microsoft Foundry**
 This step allows you to manage, observe and evaluate your agents through Microsoft Foundry. Firstly, we need to ensure the user has appropriate permissions, then we can add AI Gateway and connect Application Insights. This is one-time set-up for all your agents. 

16. Open the **Foundry project** resource in your in your resource group (default name **aca<Lab_Instance_ID>-project**)
28. Add **'Azure AI Owner'** assignment to the current user
    1. Open **Access control (IAM)** blade on the left
    2. Click **Add -> Add role assignment**
    3. Find and select role **Azure AI Owner**, click **Next**
    4. Click **+ Select members**
    5. Type your user name User1-<Lab_Instance_ID> and **select** it
    6. Press **Select**
    7. Press **Review + assign**
29. Go to **Overview** blade of the Foundry Project resource
30. Click **Go to Foundry portal** button
31. Make sure the **New Foundry** toggle at the top is **ON** -or- click the **Start building**  button on top to switch to the new Foundry portal
32. **Register your "external" agent with Foundry**
33. In the **new** Foundry portal
34. Click on **Operate** on top right
35. Click on **Admin** on the left
36. In the **All projects** tab
    1. Click on your project name to open it
    2. Click the **Connected resources** tab 
    3. Click on the **Add connection** button on the top right 
        1. Note: *If the button is not available at first, try reloading or giving a minute for all permissions to propagate*
    4. Connect the **Application Insights** by selecting your resource (keep ApiKey as Auth Type)
37. Go back to **Admin** page on the left
38. Select **AI Gateway (Preview)** tab
39. Click the **Add AI Gateway** button
    1. Select the Foundry project,
    2. Give it a unique name, that STARTS WITH **AIGateway** and select a region closest to you (like e.g. westus)
    3. Click **Add** 
    4. Takes a minute or two
40. Now click on **Assets** on the left
41. And **Register asset** button on the right
42. Fill the form 
    1. Copy  **Agent URL** from your Agentic Content Factory (Dev UI interface)
    2. Select **A2A** Protocol
    3. Copy the **A2A agent card URL** from the Dev UI interface
    4. Copy the **OpenTelemetry agent ID**
    5. You can keep Amin portal field blank or link to the Azure portal
    6. Select an existing Foundry Project and give your agent a name e.g. "research-agent"
    7. Hit **Register asset**
    8. Repeat for another agent if you'd like
43. Once the agent is registered, notice the status and version
43. Select the registered Agent and notice the property bar on the right
    1. Notice the new A2A urls from the Foundry Control plane for your agent
    2. See the **Update status** options allowing you to block the agent through the Foundry Control plane
44. Open the newly registered agent 
    1. Select the **Traces** tab and open one of the calls
    2. Explore the tools and details in this trace 

### Run evaluation in **Microsoft Foundry**
While you can set-up continuous evaluations once your agents are registered, we will run one-time evaluation of the generated social posts by this solution. This allows us to quickly explore the results.

35. Make sure you are in the new **Microsoft Foundry** portal
46. Click on **Build** on top right
47. Click on **Evaluations** on the left
48. Click on the **Create** button on  top right
    1. You can use your **Dataset** from your "Agentic Content Factory" solution
    2. Go to Dev UI, scroll to **Social posts** and **Donwnload Evals.JSONL** or grab a sample file from your repo - Lab\sample-output
    3. Back in **Foundry** click **Upload new dataset**
    4. Select the file from Downloads and give it a name
    5. You can preview the dataset on the bottom right
    6. Click **Next** to for **Field mappings** (keep default)
    7. Click **Next** to select **Criteria** or Evaluators (you can keep default)
    8.  Click **Next** and  **Submit** to run the evaluation
51. Once done, you can review the results and formulate hypothesis on what can be changed in the "Agentic Content Factory" solution

**That's it - Thanks for joining! PLEASE share your feedback via the repo**
