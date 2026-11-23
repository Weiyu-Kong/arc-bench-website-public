"""Minimal Web agent: copy this file to main.py to try the execution contract.

This creates a static page, not an implementation of the task requirements.
"""
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
