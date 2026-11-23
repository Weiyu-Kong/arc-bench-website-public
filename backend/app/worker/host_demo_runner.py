"""Run one trusted demo outside Celery/dispatcher while retaining API lifecycle state."""

from __future__ import annotations

import argparse

from app.db.session import SessionLocal
from app.core.enums import AgentSourceType, SubmissionStatus
from app.models.run import Run
from app.services.execution_service import ExecutionService
from app.services.submission_service import RunService


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("submission_id")
    parser.add_argument("--reuse-workspace", action="store_true")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        run = db.get(Run, args.submission_id)
        if run is None:
            raise RuntimeError(f"Run '{args.submission_id}' not found")
        if run.agent_source != AgentSourceType.DEMO_REPLAY.value:
            raise RuntimeError("Host demo runner can only execute demo replay runs")
        execution = ExecutionService.for_host_demo(
            db,
            run_id=run.id,
            generation=run.active_generation,
        )
        if args.reuse_workspace:
            execution.rerun_submission(args.submission_id)
        else:
            execution.run_submission(args.submission_id)
    except Exception as exc:
        # ExecutionService persists failures once a workspace exists. Cover
        # bootstrap failures as well so the UI never remains in Preparing.
        db.rollback()
        try:
            run = db.get(Run, args.submission_id)
            if run is not None and run.agent_source == AgentSourceType.DEMO_REPLAY.value:
                service = RunService(db)
                service.update_steps(
                    run,
                    service.build_failed_step_states(
                        failed_key="deploy_agent",
                        reason=str(exc),
                    ),
                )
                service.update_status(run, SubmissionStatus.FAILED, failure_reason=str(exc))
        except Exception:
            db.rollback()
        raise
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
