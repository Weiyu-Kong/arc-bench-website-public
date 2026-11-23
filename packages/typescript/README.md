# ARC-Bench TypeScript Agent Starter

Edit `index.ts` and keep it at the zip root. Keep `package.json` at the zip root; ARC-Bench runs `npm install` before your agent starts.

ARC-Bench runs your agent through the locally installed `tsx` binary:

```bash
tsx index.ts /path/to/requirements --output-dir /path/to/output
```

Your agent should read the task requirements, modify the project under `--output-dir`, and exit with code `0` when finished.

The runner injects `OPENAI_API_KEY` and `OPENAI_BASE_URL`; select model names in agent code. See `examples/model_calling.ts`.

You may use the JavaScript/TypeScript SDK package `arcbench-agent-runtime-js`, the bundled `skills/`, or both. See `examples/sdk_and_skill_usage.ts`.

## Input, output and execution

The positional argument is a directory containing requirements.yaml, with optional
requirements.md, prerequisites.md, assets/ and reference/ materials. The runner's
working directory is /workspace/template; the uploaded agent is under
/workspace/submission. Write the application into --output-dir, not the agent
folder or a nested template/ folder. Exit 0 after finishing; stdout/stderr are logs.
ARCBENCH_TASK_TYPE supplies web, cli or android; --type is optional for local use.

For Web tasks keep the provided frontend/backend build conventions, or provide a
root deploy.sh that starts the application on HOST and PORT (0.0.0.0:3000 by default).
The platform starts the app and runs its own tests after your agent exits. SDK
status events support visualization and do not replace platform evaluation.
For CLI and Android follow the matching application template instead.

## Credentials and subprocesses

Read process.env.OPENAI_BASE_URL and process.env.OPENAI_API_KEY.
Select model names in your agent code; the website injects no model-name variables.
With personal funding and the built-in proxy enabled, these contain the website
proxy URL and a temporary run credential, not your original provider key. The
backend forwards calls to the configured provider and counts usage using unified
model prices. Do not hardcode provider endpoints or keys. Vision credentials use
VISUAL_BASE_URL and VISUAL_API_KEY. Child processes inherit the
environment; when customizing it use env: { ...process.env, MY_AGENT_MODE: "build" }.
.env.example is a local reference and is not automatically loaded by this starter.
Never package real .env files or log keys. Local direct calls are not billed to a
website run. Missing usage or a missing model price prevents complete accounting.

## Packaging

ZIP the folder contents with index.js / index.ts and package.json at the root.
Keep local SDK folders referenced in package.json. For TypeScript retain tsx as a
dependency. Exclude node_modules, .git, real .env files and generated local output.
The downloaded template/ is the application scaffold, examples/ demonstrates model
calls and visualization, and skills/ contains optional workflow guidance. Evolution
tasks may include per-task baseline archives; the platform injects the authoritative
baseline at runtime. Preserve existing output when resuming or extending a task.

Full guide: /docs/agent on your ARC-Bench website. Visualization: /docs/visualization.
