# ARC-Bench Website

ARC-Bench is an educational platform for running and evaluating agent-generated applications.

This repository contains the public website application, model proxy, runner integration, frontend, deployment templates, and Python agent package. Production credentials, user data, classroom rosters, competition submissions, evaluator datasets, runtime workspaces, and private operations scripts are intentionally excluded.

## Quick start

1. Copy `config.example.yaml` to `config.yaml` and fill in deployment values through environment variables or a local ignored file.
2. Copy `source.example.sh` to a private shell file and set database, Redis, session, storage, and executor settings.
3. Start the development stack with `docker compose -f deploy/docker/compose.yaml up --build`.

For production, use `deploy/docker/compose.deploy.yaml` as a starting point and replace image names, domains, storage mounts, and secrets with deployment-specific values. Run database migrations before starting API workers and executors.

## Security

Never commit API keys, database URLs containing passwords, Redis passwords, session secrets, private email lists, user exports, runtime data, or generated submission archives.

## License

Add the project license and attribution information before publishing this repository.

## Public release scope

The public tree intentionally omits production rosters, historical leaderboard snapshots, evaluator task packs, user submissions, runtime workspaces, database exports, and administrative data tools. Deployment images and registry names are placeholders and must be configured by the operator.
