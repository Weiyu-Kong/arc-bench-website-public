"""Retire legacy competition credits when admitting or executing a run."""


def use_personal_run_credentials(run, submission=None) -> None:
    if run.billing_mode != "official_evaluation":
        return
    key = str(run.openai_api_key or getattr(submission, "openai_api_key", None) or "").strip()
    if not key:
        raise ValueError("Competition credits are no longer supported. Upload a new submission with your own API key.")
    run.openai_api_key = key
    if not run.openai_base_url and submission is not None:
        run.openai_base_url = submission.openai_base_url
    run.billing_mode = "self_funded"
    run.budget_competition_entry_id = None
