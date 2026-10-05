"""Local, file-based scientific workflows using the user's existing agent client."""

from .workspace import (load_dataset, load_jobs, load_manifest, load_snapshot, next_job,
                        pending_job_count, release_job, update_run_status)

__all__ = ["load_dataset", "load_jobs", "load_manifest", "load_snapshot", "next_job",
           "pending_job_count", "release_job", "update_run_status", "prepare_workspace", "refresh_workspace",
           "submit_response", "finalize_workspace", "synthesize_workspace"]


def __getattr__(name):
    if name == "synthesize_workspace":
        from .synthesis import synthesize_workspace
        return synthesize_workspace
    if name in {"prepare_workspace", "refresh_workspace", "submit_response", "finalize_workspace"}:
        from . import workflow
        return getattr(workflow, name)
    raise AttributeError(name)
