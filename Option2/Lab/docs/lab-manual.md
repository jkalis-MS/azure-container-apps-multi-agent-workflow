## About the lab...

A multi-agent content factory that researches Microsoft technology topics, creates multi-format content, and optimizes output quality. The outcome is to familiarize yourself with running and hosting AI Agents on Azure Container Apps, explore agent observability, and register and evaluate agents in Microsoft Foundry.

## Architecture
```
[Dev UI] --> [Agent 1 - Researcher] --> [Agent 2 - Content Creator] --> [Agent 3 - Podcaster]
```

## What It Does

Enter a topic (e.g. "Write a comprehensive blog post about Azure Container Apps for developers."). Three agents collaborate:

1. **Agent 1 — Tech Research** (LangGraph / Python): Searches Microsoft Learn, Azure Blog, Tech Community, Azure Updates, and GitHub Azure-Samples. Uses AI for intent detection (extracting topic and target audience from the query), ranks sources by relevance, fetches content from the top hits, and synthesizes a debrief.
2. **Agent 2 — Content Creator** (Microsoft Agent Framework / .NET): Transforms the research brief from Agent 1 into an original blog post and social media posts — all grounded in real sources.
3. **Agent 3 — Podcaster** (GitHub Copilot CLI SDK / Python): Creates an engaging podcast about the desired topic. It can either use a text-to-speech service from Microsoft Foundry (default) or a custom text-to-speech server running on serverless GPUs on Azure Container Apps.

Agents communicate via the A2A (Agent-to-Agent) protocol — each exposes a `/.well-known/agent.json` card for discovery and a `/a2a` JSON-RPC endpoint for task submission. Each agent runs as a separate container on Azure Container Apps.

## Instructions

**In this lab, we first deploy the solution to Azure using `azd up`, then explore it.**

### Deploy to Azure

1. **Log in to Azure** from the VS Code terminal:

    ```bash
    az login
    ```

2. **Deploy the solution** with the Azure Developer CLI:

    ```bash
    cd Option2
    azd up
    ```

    - Pick a subscription and a region (e.g. `westus3`) when prompted.
    - Provisioning + container builds take ~10 minutes. Yellow warnings are OK.

3. While the deployment runs, explore the project structure and architecture in VS Code.

### Explore your deployment on the **Azure portal**

Open the app you deployed and use the Agentic Content Factory to generate your first content.

4. Open the **Azure portal** and navigate to your resource group (e.g. `rg-aca-mvp`).
5. Open the **`aca-mvp-dev-ui`** Container App.
    1. Click the **Application Url** in the top-right of the **Overview** blade to open the Dev UI.
6. The Dev UI front page opens.
    1. Wait until all 3 agent status lights are green.
7. Type your prompt — e.g. **"Azure Container Apps for developers"**.
8. Start exploring results once they are available. You can also review the sample output in your repo under `Lab/sample-output`.
9. Click **Copy DevUI Config** to capture the agent endpoints — you will use these when registering agents in Foundry.

### Observability with **Application Insights**

10. Open the **Application Insights** resource called **`aca-mvp-appinsights`** in your resource group.
    1. From the left navigation open **Investigate → Agents (preview)**.
    2. Explore all agent and tool calls and token usage of your agents.
    3. *Tip: changing the **Time range** to the last 15 or 30 minutes often gives a better view.*
11. Don't forget to click **Explore in Grafana** at the bottom for deeper details including traces.

### Register agents in **Microsoft Foundry**

This step lets you manage, observe, and evaluate your agents through Microsoft Foundry. First, ensure the user has appropriate permissions, then add an AI Gateway and connect Application Insights. This is a one-time setup for all your agents.

12. Open the **Foundry project** resource in your resource group (default name **`aca-mvp-project`**).
13. Add an **Azure AI Owner** role assignment to the current user:
    1. Open the **Access control (IAM)** blade on the left.
    2. Click **Add → Add role assignment**.
    3. Find and select the **Azure AI Owner** role, then click **Next**.
    4. Click **+ Select members**.
    5. Search for your user name and **select** it.
    6. Press **Select**.
    7. Press **Review + assign**.
14. Go to the **Overview** blade of the Foundry Project resource.
15. Click the **Go to Foundry portal** button.
16. Make sure the **New Foundry** toggle at the top is **ON** — or click the **Start building** button at the top to switch to the new Foundry portal.
17. **Register your "external" agent with Foundry.** In the new Foundry portal:
    1. Click **Operate** on the top right.
    2. Click **Admin** on the left.
    3. In the **All projects** tab, click your project name to open it.
    4. Click the **Connected resources** tab.
    5. Click the **Add connection** button on the top right.
        - *If the button is not available at first, try reloading or wait a minute for permissions to propagate.*
    6. Connect **Application Insights** by selecting your resource (keep API key as Auth Type).
18. Go back to the **Admin** page on the left.
19. Select the **AI Gateway (Preview)** tab.
20. Click the **Add AI Gateway** button:
    1. Select the Foundry project.
    2. Give it a unique name that starts with **`AIGateway`** and select a region close to you (e.g. `westus`).
    3. Click **Add** (takes a minute or two).
21. Click **Assets** on the left.
22. Click the **Register asset** button on the right.
23. Fill out the form:
    1. Copy the **Agent URL** from your Agentic Content Factory (Dev UI interface).
    2. Select **A2A** as the Protocol.
    3. Copy the **A2A agent card URL** from the Dev UI interface.
    4. Copy the **OpenTelemetry agent ID**.
    5. Leave the **Admin portal** field blank or link to the Azure portal.
    6. Select an existing Foundry Project and give your agent a name, e.g. `research-agent`.
    7. Hit **Register asset**.
    8. Repeat for another agent if you'd like.
24. Once the agent is registered, notice the status and version.
25. Select the registered agent and notice the property bar on the right:
    1. Notice the new A2A URLs from the Foundry control plane for your agent.
    2. See the **Update status** options that allow you to block the agent through the Foundry control plane.
26. Open the newly registered agent:
    1. Select the **Traces** tab and open one of the calls.
    2. Explore the tools and details in this trace.

### Run evaluation in **Microsoft Foundry**

While you can set up continuous evaluations once your agents are registered, here we'll run a one-time evaluation of the social posts generated by this solution to quickly explore the results.

27. Make sure you are in the new **Microsoft Foundry** portal.
28. Click **Build** on the top right.
29. Click **Evaluations** on the left.
30. Click the **Create** button on the top right:
    1. You can use your **dataset** from the Agentic Content Factory.
    2. Go to the Dev UI, scroll to **Social posts**, and click **Download Evals.JSONL** — or grab the sample file from your repo at `Lab/sample-output`.
    3. Back in **Foundry**, click **Upload new dataset**.
    4. Select the file from Downloads and give it a name.
    5. You can preview the dataset on the bottom right.
    6. Click **Next** to move to **Field mappings** (keep defaults).
    7. Click **Next** to select **Criteria** or evaluators (you can keep defaults).
    8. Click **Next** and **Submit** to run the evaluation.
31. Once the evaluation is done, review the results and form a hypothesis on what could be changed in the Agentic Content Factory solution.

**That's it — thanks for joining! Please share your feedback via the repo.**
