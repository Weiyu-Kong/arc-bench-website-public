"""Run inside a published runner image before promoting it to evaluation traffic."""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
import shutil
import subprocess


EXPECTED_PLAYWRIGHT_VERSION = "1.57.0"
MINIMUM_NODE_VERSION = (22, 12, 0)


def main() -> int:
    required = ("node", "npm", "python3", "git")
    missing = [command for command in required if shutil.which(command) is None]
    if missing:
        raise RuntimeError(f"Missing required runner commands: {', '.join(missing)}")

    node_runtime_version = subprocess.check_output(["node", "--version"], text=True).strip()
    if tuple(int(part) for part in node_runtime_version.lstrip("v").split(".")) < MINIMUM_NODE_VERSION:
        raise RuntimeError(f"Node.js 22.12.0+ is required, found {node_runtime_version}")

    templates_root = Path("/opt/arcbench/templates")
    required_templates = ("web-react-express", "mobile-android-java", "cli-python")
    missing_templates = [name for name in required_templates if not (templates_root / name).is_dir()]
    if missing_templates:
        raise RuntimeError(f"Missing required ARC templates: {', '.join(missing_templates)}")

    python_version = version("playwright")
    node_version = subprocess.check_output(
        ["node", "-p", "require('/opt/arcbench/node_modules/@playwright/test/package.json').version"],
        text=True,
    ).strip()
    if python_version != EXPECTED_PLAYWRIGHT_VERSION or node_version != EXPECTED_PLAYWRIGHT_VERSION:
        raise RuntimeError(
            "Playwright package versions do not match the runner image: "
            f"expected={EXPECTED_PLAYWRIGHT_VERSION}, python={python_version}, node={node_version}"
        )

    browser_check = subprocess.run(
        [
            "python3",
            "-c",
            "from playwright.sync_api import sync_playwright; "
            "p=sync_playwright().start(); b=p.chromium.launch(channel='chromium'); b.close(); p.stop()",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if browser_check.returncode != 0:
        raise RuntimeError(f"Python Playwright Chromium launch failed:\n{browser_check.stdout}")

    node_browser_check = subprocess.run(
        [
            "node",
            "-e",
            "const { chromium } = require('/opt/arcbench/node_modules/@playwright/test'); "
            "(async () => { const b = await chromium.launch({ channel: 'chromium' }); await b.close(); })()"
            ".catch((error) => { console.error(error); process.exit(1); });",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if node_browser_check.returncode != 0:
        raise RuntimeError(f"Node Playwright Chromium launch failed:\n{node_browser_check.stdout}")

    print(
        "Runner smoke test passed: Python, Node.js, Git, "
        f"Playwright {EXPECTED_PLAYWRIGHT_VERSION}, and Chromium are available."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
