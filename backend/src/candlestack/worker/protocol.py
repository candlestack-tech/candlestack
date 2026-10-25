"""What the worker's loop and the process of a job say to each other, without Keras: the loop
imports this, never the runner."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from candlestack.jobs import Job, JobResult


class Request(BaseModel):
    """What the loop gives the process on stdin: the job and where the files are."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job: Job
    models_dir: Path
    files_dir: Path


class Outcome(BaseModel):
    """What the process gives back on stdout: the result, when its work began (after its
    imports, epoch seconds), and the traceback of a failure of ours, for the loop to log."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    result: JobResult
    began_at: float
    trace: str | None = None
