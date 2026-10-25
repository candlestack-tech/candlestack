"""What the backend and the worker say to each other: a job, its result, and the Redis keys
they meet at (docs/architecture.md, "Worker").

A job names the model's file (relative to ``MODEL_STORAGE_DIR``) with the SHA-256 it was
uploaded with, carries the manifest as data, and for ``predict`` has candles in its directory
of the shared volume (``files.py``). Both are JSON in Redis; neither holds candles or
predictions, which go through the volume.
"""

import time
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

JobKind = Literal["predict", "check"]
JobStatus = Literal["done", "failed"]

# Why a job failed, for the logs and the code that reads the result; ``error`` says it to
# the user.
Reason = Literal[
    "model-missing",  # no file at the job's path: the model was deleted
    "model-changed",  # the file's SHA-256 is not the job's
    "manifest",  # the manifest is not valid or cannot run
    "candles",  # the candles break their rules or are too few for one sample
    "load",  # Keras refused the file
    "predict",  # the model failed on its input
    "output",  # the model's output has the wrong shape or numbers that are not finite
    "example",  # the output for the manifest's example is not the expected one
    "time-limit",  # the job ran longer than JOB_TIME_LIMIT and was stopped
    "stopped",  # the job's process ended without a result (its memory, a crash)
    "worker-stopped",  # the worker stopped or lost the job while it ran
    "expired",  # the job waited past its deadline: nobody waits for it any more
    "internal",  # a bug of ours: logged as an error
]

# What the backend waits for at most by default: the queue, the start of the job's process
# (Keras' import, about 2 s) and the run.
DEFAULT_WAIT = 180.0
# The output of the manifest's example must be within this of the expected one.
EXAMPLE_TOLERANCE = 1e-4

# The keys, all under "jobs:". The queue and the running list hold job IDs; the job itself is
# a string of its own, a lease marks a job a live worker runs, and its result is a list of one
# that the backend pops (BLPOP), so waiting needs no polling of a key.
QUEUE = "jobs:queue"
RUNNING = "jobs:running"
JOB_PREFIX = "jobs:job:"
LEASE_PREFIX = "jobs:lease:"
RESULT_PREFIX = "jobs:result:"

# A result nobody popped (the backend stopped waiting) goes after this, in seconds.
RESULT_TTL = 600

# The model files are ``<user_id>/<model_id>.keras``: no absolute path, no "..", no other
# extension.
MODEL_PATH = r"^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*\.keras$"
SHA256 = r"^[0-9a-f]{64}$"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Job(_Frozen):
    """One job for the worker.

    ``deadline`` is when the backend stops waiting (epoch seconds): a job still in the queue
    then is failed as ``expired`` without running, so a backlog after an outage costs no
    runs nobody reads.
    """

    version: Literal[1] = 1
    id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")] = Field(
        default_factory=lambda: uuid.uuid4().hex
    )
    kind: JobKind
    model: Annotated[str, Field(pattern=MODEL_PATH, max_length=200)]
    sha256: Annotated[str, Field(pattern=SHA256)]
    manifest: dict[str, Any]
    enqueued_at: float = Field(default_factory=time.time)
    deadline: float


class Runner(_Frozen):
    """What ran the model: the Keras release, its backend and the backend's release."""

    keras: str
    backend: str
    backend_version: str


class Timings(_Frozen):
    """Where the time of a job went, in milliseconds; None for a step it did not reach.

    ``queued`` until a worker took it, ``start`` the job's process and its imports, ``load``
    the model, ``input`` the preprocessing, ``predict`` the model, ``total`` from taking the
    job to its result.
    """

    queued: float | None = None
    start: float | None = None
    load: float | None = None
    input: float | None = None
    predict: float | None = None
    total: float | None = None


class JobResult(_Frozen):
    """What became of a job.

    - ``samples``: the predictions made (``predict``: the rows of the predictions file;
      ``check``: of the trial run on the built-in candles);
    - ``dropped``: the candles at the start without a sample (the warm-up);
    - ``verified`` (``check``): the model gave the manifest's ``example`` output within
      ``EXAMPLE_TOLERANCE``; False without an example, None when the check failed earlier;
    - ``example_difference`` (``check``): the largest difference from the example's output;
    - ``error``: what went wrong in plain sentences, for the user; ``reason`` names it.
    """

    id: str
    kind: JobKind
    status: JobStatus
    reason: Reason | None = None
    error: str | None = None
    samples: int | None = None
    dropped: int | None = None
    verified: bool | None = None
    example_difference: float | None = None
    runner: Runner | None = None
    timings: Timings = Timings()

    @classmethod
    def failed(cls, job_id: str, kind: JobKind, reason: Reason, error: str) -> "JobResult":
        return cls(id=job_id, kind=kind, status="failed", reason=reason, error=error)
