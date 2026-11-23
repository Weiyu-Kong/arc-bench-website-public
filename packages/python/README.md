# ARC-Bench Python Blank

## Start with Blank

Choose **Download Template → Blank Template** on a task or competition page. Select the same language when uploading. Edit the entrypoint, then ZIP the contents of the extracted folder. Blank is a runnable scaffold; implementing requirements is your agent's responsibility.

## Package contents

~~~text
main.py                         Python agent entrypoint
requirements.txt                Agent dependencies
README.md                       Input, output and execution guide
.env.example                    Local environment examples, without secrets
arcbench-agent-runtime/         Local visualization SDK
examples/model_calling.py        Model calls using injected environment variables
examples/minimal_agent.py        Complete minimal Web agent
examples/sdk_and_skill_usage.py  Events, traceability, Git and skills
skills/                         Optional workflow guidance
template/                      Application scaffold for the task type
~~~

The root entrypoint is the agent that builds the application. template/ contains its starting application. Evolution downloads may include evolution-templates/manifest.json and baseline ZIPs. The platform injects the authoritative baseline again at execution time.

## Required upload files

| Language | Required at ZIP root | Execution |
| --- | --- | --- |
| Python | main.py, requirements.txt | Install requirements.txt, then python3 main.py |
| JavaScript | index.js, package.json | npm install, then node index.js |
| TypeScript | index.ts, package.json | npm install, then local node_modules/.bin/tsx; include tsx in dependencies |

Keep local SDK folders referenced by dependency files. Package the files directly at the ZIP root. Exclude node_modules, .venv, caches, .git and real .env files. Agent dependencies and generated application dependencies belong in their respective project directories.

~~~bash
zip -r agent.zip . -x 'agent.zip' '.env' '.env.local' 'node_modules/*' '.venv/*' '.git/*' '__pycache__/*'
~~~

## Input contract

The current working directory is /workspace/template. The runner launches an absolute entrypoint, for example:

~~~bash
python3 /workspace/submission/main.py /tmp/arcbench/requirements-source --output-dir /workspace/template
~~~

The positional argument is a **requirements directory**, not a JSON payload or YAML file. Read requirements.yaml inside it, and any available requirements.md, prerequisites.md, assets/ or reference/ materials. Prefer the positional argument over environment paths.

--output-dir is the application root. ARCBENCH_TASK_TYPE provides web, cli or android. Blank also accepts --type for local use; the platform does not always pass that flag.

A small requirements.yaml example is included under examples/requirements/:

~~~yaml
id: ROOT
name: Example Web application
type: FOLDER
dependencies: []
description: A small application.
children:
  - id: REQ-1
    name: Welcome page
    type: ATOMIC
    dependencies: []
    description: Show a page with the heading "Welcome".
    children: []
~~~

Node IDs identify requirements, children forms the tree and description specifies behavior. Actual tasks can include additional fields and materials; read the whole input rather than hardcoding these example nodes.

## Output contract

Write a runnable application into --output-dir and exit with code 0 on completion, nonzero on failure. stdout and stderr are logs, with no required result JSON. Printing model output alone does not create an application. Do not write results under submission/ or nest the application under output/template/.

For Web tasks, retain the downloaded frontend/ and backend/ build/start conventions. The runner builds the frontend and starts the backend on 0.0.0.0:3000. If the output root contains deploy.sh, it runs bash deploy.sh instead. That script must prepare and start the app, honor HOST and PORT, and keep the server running. CLI and Android tasks follow their own downloaded scaffold conventions.

## A minimal agent

Copy examples/minimal_agent.py to main.py to try the complete contract. It reads requirements.yaml, creates an index.html and deploy.sh, then exits. The platform starts the resulting app. This standard-library example makes no model calls and does not solve the business requirements; it demonstrates input and output only. The full source and local commands are included in the download.

~~~bash
python3 -m pip install -r requirements.txt
# A local input example is included under examples/requirements/
python3 examples/minimal_agent.py ./examples/requirements --output-dir ./local-output
bash ./local-output/deploy.sh
~~~

The default main.py copies the bundled template without calling a model. Implement run_agent to read requirements, call models, modify application files and finish. Preserve existing output when continuing a run or extending an evolution baseline.

~~~python
import argparse
import html
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("requirement_path")
parser.add_argument("--output-dir", required=True)
parser.add_argument("--type", default=os.getenv("ARCBENCH_TASK_TYPE", "web"))
args = parser.parse_args()

requirements = (Path(args.requirement_path) / "requirements.yaml").read_text(encoding="utf-8")
output = Path(args.output_dir)
output.mkdir(parents=True, exist_ok=True)
(output / "index.html").write_text(
    "<!doctype html><meta charset='utf-8'><h1>My first agent</h1><pre>"
    + html.escape(requirements) + "</pre>", encoding="utf-8",
)
(output / "deploy.sh").write_text(
    'set -eu\ncd "$(dirname "$0")"\nexec python3 -m http.server "${PORT:-3000}" --bind "${HOST:-0.0.0.0}"\n',
    encoding="utf-8",
)
print("Application files are ready", flush=True)
~~~

## Base URL, API key and model usage

For personal funding, supply the upstream Base URL and API key in the website; select model names in your agent code. When the built-in proxy is enabled, the backend retains the upstream credentials and issues a temporary run credential. OPENAI_BASE_URL inside the container points to the website proxy, for example https://arc-bench.com/api/model-proxy/v1. OPENAI_API_KEY is temporary. No MODEL or VISUAL_MODEL environment variable is injected.

Read the injected values directly. The proxy forwards requests using the saved upstream credentials, accumulates tokens from response usage and applies the website's unified prices. Hardcoding a provider URL or key bypasses this accounting path. Temporary credentials are revoked when execution ends. Without the proxy, resolved upstream credentials are passed directly. The website does not choose a main or vision model.

~~~python
import os
from openai import OpenAI

client = OpenAI(base_url=os.environ["OPENAI_BASE_URL"], api_key=os.environ["OPENAI_API_KEY"])
result = client.chat.completions.create(
    model="your-model-name",  # Choose a model supported by your API endpoint/key.
    messages=[{"role": "user", "content": "Plan an implementation for the requirements."}],
)
print(result.choices[0].message.content)
~~~

Only use Responses, vision or structured output when supported by your upstream. Never log or embed keys in generated files. Complete cost accounting requires recognizable usage and a matching model price. Unavailable costs are treated as infinity in competition scoring.

## Injected environment

| Variables | Purpose |
| --- | --- |
| OPENAI_BASE_URL, OPENAI_API_KEY | Main model connection; read without overriding |
| VISUAL_BASE_URL, VISUAL_API_KEY | Visual model connection, possibly through the proxy |
| ARCBENCH_PROJECT_DIR, ARCBENCH_OUTPUT_DIR | Application root, normally /workspace/template |
| ARCBENCH_SUBMISSION_DIR | Uploaded agent root, normally /workspace/submission |
| ARCBENCH_TASK_DIR, ARCBENCH_REQUIREMENT_DIR | Relative requirements directory; prefer the CLI positional argument |
| ARCBENCH_TASK_TYPE | web, cli or android |
| ARCBENCH_ARC_DIR | Runtime artifacts, normally application/.arc/ |
| ARCBENCH_RUNNER_EVENTS_PATH, ARCBENCH_TRACEABILITY_DIR | Visualization events and snapshots, consumed by the SDK |

Child processes inherit these variables by default. Preserve the environment when setting extra values:

~~~python
import os
import subprocess
subprocess.run(["python3", "worker.py"], env={**os.environ, "MY_AGENT_MODE": "build"}, check=True)
~~~

.env.example is a local reference, not automatically loaded by Python. Export variables in your shell before running model examples. Local direct upstream calls are not automatically attributed to a website run.

## After upload

1. Save the agent package and model configuration, then start a task evaluation. A competition submission can run multiple tasks.
2. The platform queues execution, prepares an isolated workspace and injects requirements and the appropriate application baseline.
3. It installs agent dependencies, injects environment variables and launches the entrypoint with the fixed arguments.
4. Your agent edits the output application. Logs stream to the website; optional SDK calls update visualization and Git history.
5. After the agent exits, the platform builds or starts the application and runs its own tests. SDK success events do not replace those tests.
6. The website displays test results, usage, costs and artifacts. Competition scores use the platform scoring rules.


中文说明：[README.zh-CN.md](README.zh-CN.md)。
