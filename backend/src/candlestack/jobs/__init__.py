"""Jobs: how the backend hands a model to the worker that runs it, and gets its predictions.

A job queue in Redis of its own (``JOBS_REDIS_URL``) carries the jobs and their results; the
candles and the predictions go through a volume both share (``JOB_FILES_DIR``). The backend
uses ``JobClient`` (``predict``, ``check``); the worker (``candlestack.worker``) uses
``JobQueue`` and ``JobFiles`` itself. Nothing here imports a deep learning framework or another
module of the project: the API image runs it without Keras.

Other modules import only from here, never from the submodules.
"""

from candlestack.jobs.client import NO_ANSWER, JobClient
from candlestack.jobs.files import CANDLES, PREDICTIONS, JobFiles
from candlestack.jobs.protocol import (
    DEFAULT_WAIT,
    EXAMPLE_TOLERANCE,
    Job,
    JobKind,
    JobResult,
    JobStatus,
    Reason,
    Runner,
    Timings,
)
from candlestack.jobs.queue import WORKER_STOPPED, Claimed, JobQueue

__all__ = [
    "CANDLES",
    "DEFAULT_WAIT",
    "EXAMPLE_TOLERANCE",
    "NO_ANSWER",
    "PREDICTIONS",
    "WORKER_STOPPED",
    "Claimed",
    "Job",
    "JobClient",
    "JobFiles",
    "JobKind",
    "JobQueue",
    "JobResult",
    "JobStatus",
    "Reason",
    "Runner",
    "Timings",
]
