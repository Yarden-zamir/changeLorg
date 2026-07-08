# changelorg Summary: 2026-06-29 to 2026-07-06

Generated from the live changelorg cache. Focus: relevant and interesting changes from the past 7 days.

## Highlights

- Google published a cluster of Gemma/Gemini-adjacent developer updates: Gemma 4 12B, DiffusionGemma, local agentic workflows on laptops, and a community post on training Gemma to reason with Tunix and TPUs.
- GitHub Copilot had several enterprise/admin changes: improved usage metrics, agent session streaming preview, default auto model selection, AI credit pools, and Copilot CLI support for `GITHUB_TOKEN` in Actions.
- GitHub announced Gemini model deprecations in Copilot: Gemini 2.5 Pro and Gemini 3 Flash are scheduled for removal from Copilot experiences on 2026-07-31.
- GitHub Models is moving toward full retirement on 2026-07-30, continuing the earlier shutdown path for that product.
- Slack developer platform is pushing harder into agents: app messages can now receive current user context, and Slack introduced a new agent messaging experience.
- OpenAI published several notable research/engineering posts, including ChatGPT adoption data, GeneBench-Pro, and a deep infrastructure debugging story using core dumps.

## AI And Agent Tooling

- [Gemma 4 12B: The Developer Guide](https://developers.googleblog.com/gemma-4-12b-the-developer-guide/) introduces a dense multimodal Gemma model aimed at high-performance local execution.
- [Bringing Gemma 4 12B to your Laptop](https://developers.googleblog.com/bringing-gemma-4-12b-to-your-laptop-unlocking-local-agentic-workflows-with-google-ai-edge/) focuses on local agentic workflows with Google AI Edge and 16GB consumer laptops.
- [DiffusionGemma](https://developers.googleblog.com/diffusiongemma-the-developer-guide/) explores diffusion-style text generation instead of traditional autoregressive token generation.
- [Google Colab CLI](https://developers.googleblog.com/introducing-the-google-colab-cli/) connects local terminals and AI agents to remote Colab runtimes.
- [Slack agent context](https://docs.slack.dev/changelog/2026/07/02/app-context) lets Slack agent apps receive context about what a user is viewing when they message the app.
- [Slack CLI v4.4.0](https://docs.slack.dev/changelog/2026/06/30/slack-cli) shipped alongside Slack's new agent messaging experience.

## GitHub And Copilot

- [Copilot usage metrics improved](https://github.blog/changelog/2026-07-02-improved-accuracy-and-coverage-in-copilot-usage-metrics-reports): more complete reporting, including Copilot CLI suggested lines.
- [Copilot CLI in Actions](https://github.blog/changelog/2026-07-02-copilot-cli-no-longer-needs-a-personal-access-token-in-github-actions) no longer needs a PAT and can use `GITHUB_TOKEN`.
- [Copilot agent session streaming](https://github.blog/changelog/2026-07-02-copilot-agent-session-streaming-is-now-in-public-preview) entered public preview for GitHub Enterprise Cloud managed users.
- [Enterprise auto model selection](https://github.blog/changelog/2026-07-01-enterprises-can-default-to-auto-model-selection) can now be configured as the default for Copilot conversations.
- [AI credit pools for cost centers](https://github.blog/changelog/2026-07-02-cost-centers-now-support-included-usage-caps) allow enterprises to cap included AI credit usage per cost center.
- [Issue fields](https://github.blog/changelog/2026-07-02-issue-fields-are-now-generally-available) are now generally available across GitHub organization plans.

## Security And Governance

- [GitHub secret scanning inbox zero](https://github.blog/security/application-security/how-github-used-secret-scanning-to-reach-inbox-zero/) describes how GitHub remediated 20,000+ secret scanning alerts across 15,000 repositories.
- [Secret scanning public monitoring](https://github.blog/changelog/2026-07-01-secret-scanning-public-monitoring-for-enterprises) helps enterprises detect leaked secrets outside their own repositories.
- [Six security settings every maintainer should enable](https://github.blog/security/6-security-settings-every-github-maintainer-should-enable-this-week/) is a practical checklist for repository security hardening.
- [GitHub license compliance](https://github.blog/enterprise-software/governance-and-compliance/how-github-maintains-compliance-for-open-source-dependencies/) explains GitHub's internal and product approach to open source dependency policy.
- [Advisory Database volume](https://github.blog/security/supply-chain-security/inside-the-advisory-database-and-what-happens-when-vulnerability-volume-breaks-records/) covers the surge in vulnerability reports and database processing pressure.

## Developer Tool Releases

- [uv 0.11.26](https://github.com/astral-sh/uv/releases/tag/0.11.26) shipped performance work around PubGrub dependency solving and allocation avoidance.
- [ty 0.0.56](https://github.com/astral-sh/ty/releases/tag/0.0.56) shipped bug fixes around MRO cycles, NamedTuple fields, and type modeling.
- [FastAPI 0.139.0](https://github.com/fastapi/fastapi/releases/tag/0.139.0) added support for dependencies in `app.frontend()`, useful for frontend auth patterns such as cookies.
- [FastAPI 0.138.2](https://github.com/fastapi/fastapi/releases/tag/0.138.2) refined `app.frontend()` behavior for non-GET/HEAD methods with no static file match.
- [VS Code 1.127](https://code.visualstudio.com/updates/v1_127) and [1.128 Insiders](https://code.visualstudio.com/updates/v1_128) were published.
- [IntelliJ IDEA 2026.1.4](https://blog.jetbrains.com/idea/2026/07/intellij-idea-2026-1-4/) shipped with fixes.

## Apps And Platforms

- [GitHub Desktop 3.6.0](https://github.com/desktop/desktop/releases/tag/release-3.6.0) added Git worktree support.
- [GitHub Desktop 3.6.2](https://github.com/desktop/desktop/releases/tag/release-3.6.2) fixed worktree command-line behavior and repository list scrolling.
- [GitHub Desktop 3.6.3-beta1](https://github.com/desktop/desktop/releases/tag/release-3.6.3-beta1) continued repository list scrolling fixes and clarified line-ending warnings.
- [Python install manager 26.3](https://www.python.org/downloads/release/pymanager-263/) was released.
- GitHub Status reported resolved incidents affecting Pages, Copilot budget reset delays, and signup flow/service performance.

## Watch Closely

- GitHub's AI surface is changing quickly: Copilot model defaults, model deprecations, agent session telemetry, and GitHub Models retirement all landed in the same week.
- Local AI tooling is gaining momentum: Gemma 4 12B, DiffusionGemma, Google AI Edge, and Colab CLI are all relevant to local/agentic workflows.
- Slack's platform changes suggest agent apps are becoming a first-class interaction model, not just bot-style message handlers.
