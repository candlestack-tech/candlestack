"""The runner in a process of its own: what the API processes and the runner tell each other
through Redis (docs/architecture.md, "The queue").

``APP_ROLE`` decides where the runs are. ``all`` (the default, and local development) runs the
API and the runner in one process, as one granian worker: the runner fills the slots itself and
tells the waits of its process when a backtest changed. With more granian workers every process
would run its own runner, which would fail and queue again the runs of the others and give
each process the slots of all; so prod and stage run the API as ``api`` in as many workers as
``WEB_WORKERS`` says, and one process ``runner`` in a container of its own. An ``api`` process
keeps a ``RemoteRunner`` where the runner was (``app.state.backtest_runner``): it has no slots
and no runs, and asks the runner through Redis pub/sub (``RunnerChannel``):

- ``wake`` (api to runner): fill the free slots, after a start or a sweep committed new
  backtests or a delete freed a slot. It carries a token that the runner answers on ``filled``
  once the fill that began after it has ended, so a start still says ``running`` when its
  backtest got a slot (``RemoteRunner.fill`` waits for it ``FILL_WAIT`` at most, then the start
  answers what is stored);
- ``stop`` (api to runner): the IDs of running backtests that were deleted, whose runs it
  cancels;
- ``changed`` (runner and api to every api): the IDs of backtests that started, ended or were
  cancelled, for the waits of the MCP tools (``Watchers``): each process subscribes once and
  sets the events of its own waits.

Pub/sub keeps no message for a subscriber that is not there, and every message has a net that
does without it: the runner fills the slots and puts back the orphans every 30 seconds and
fills them whenever it (re)subscribes, its checks cancel the runs of backtests that are gone, a
wait reads the statuses every 2.5 seconds anyway, and a start that hears no ``filled`` answers
its backtest as stored (``queued``, which is true: it starts at the runner's next fill). A
stream or a list would keep the messages for a runner that restarts, but a runner that starts
fills the slots from Postgres, where the truth is, so nothing would read them.
"""

import asyncio
import logging
import secrets
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager, suppress
from typing import Protocol

import anyio
from redis.asyncio import Redis
from redis.exceptions import RedisError

from candlestack.core import Database
from candlestack.experiments.store import BacktestStore
from candlestack.jobs import JobClient

logger = logging.getLogger(__name__)

WAKE = "wake"
STOP = "stop"
CHANGED = "changed"
FILLED = "filled"
# How long a start waits for the runner's fill before it answers what is stored, in seconds.
FILL_WAIT = 1.0
# How long a subscription waits for a message in one read, in seconds: below the socket
# timeout of the app's Redis client (``core.create_redis``).
READ_WAIT = 1.0
# The pause before subscribing again after Redis failed, doubled up to the last, in seconds.
RETRY_FIRST = 0.5
RETRY_LAST = 30.0


def channel_prefix(app_env: str) -> str:
    """The prefix of the environment's channels: pub/sub ignores the Redis database, so a
    local backend and the tests on one Redis must not hear each other."""
    return f"candlestack:{app_env}:runner"


class Runner(Protocol):
    """What the API's services ask of the runner, wherever it runs: ``BacktestRunner`` in the
    process (``APP_ROLE`` ``all``) or ``RemoteRunner`` in an ``api`` process."""

    async def ping(self) -> None:
        """Raises ``redis.exceptions.RedisError`` when the worker's queue cannot be reached."""
        ...

    async def free_slot(self) -> bool:
        """Whether a run slot is free now."""
        ...

    async def fill(self) -> None:
        """Gives the free slots to the queues in turn and returns once that is done (from
        another process: once the runner said so, or after ``FILL_WAIT``)."""
        ...

    def stop(self, backtest_ids: Collection[str]) -> None:
        """Cancels the runs of the backtests, deleted while they ran."""
        ...

    def changed(self, backtest_id: str) -> None:
        """Tells the waits for the backtest that it changed."""
        ...

    def watch(self, backtest_ids: Iterable[str]) -> AbstractContextManager[asyncio.Event]:
        """An event set whenever one of the backtests changes, while the block runs."""
        ...


class Watchers:
    """The waits of one process for backtests to change, by the backtest's ID."""

    def __init__(self) -> None:
        self.waiting: dict[str, set[asyncio.Event]] = {}

    @contextmanager
    def watch(self, backtest_ids: Iterable[str]) -> Iterator[asyncio.Event]:
        """An event set whenever one of the backtests starts, ends or is cancelled, while the
        block runs: a wait clears it, reads the backtests, and waits for it (and still reads
        again now and then, as the word can be lost)."""
        event = asyncio.Event()
        watched = set(backtest_ids)
        for backtest_id in watched:
            self.waiting.setdefault(backtest_id, set()).add(event)
        try:
            yield event
        finally:
            for backtest_id in watched:
                events = self.waiting.get(backtest_id)
                if events is not None:
                    events.discard(event)
                    if not events:
                        del self.waiting[backtest_id]

    def changed(self, backtest_id: str) -> None:
        """Tells the waits for the backtest that it changed."""
        for event in self.waiting.get(backtest_id, ()):
            event.set()


class RunnerChannel:
    """The messages between the runner and the API processes: Redis pub/sub on the channels
    ``<prefix>:<kind>``, each message the words of its kind separated by spaces (backtest IDs,
    a token)."""

    def __init__(self, redis: Redis, prefix: str) -> None:
        self._redis = redis
        self._prefix = prefix
        # The messages being sent, so none is collected before it is.
        self._sending: set[asyncio.Task[None]] = set()

    def _channel(self, kind: str) -> str:
        return f"{self._prefix}:{kind}"

    async def publish(self, kind: str, words: Iterable[str]) -> None:
        """Sends the words; Redis out of reach is a warning, as each message has its net."""
        try:
            await self._redis.publish(self._channel(kind), " ".join(words))
        except (RedisError, OSError) as error:
            logger.warning("The runner's %s was not sent (%s)", kind, type(error).__name__)

    def send(self, kind: str, words: Iterable[str]) -> None:
        """``publish`` in a task of its own, for those that cannot wait for it."""
        task = asyncio.create_task(self.publish(kind, list(words)), name=f"runner {kind}")
        self._sending.add(task)
        task.add_done_callback(self._sending.discard)

    async def close(self) -> None:
        """Sends what is being sent, a second at most."""
        with anyio.move_on_after(1):
            await asyncio.gather(*self._sending, return_exceptions=True)

    async def listen(
        self,
        handlers: Mapping[str, Callable[[list[str]], None]],
        subscribed: Callable[[], None] = lambda: None,
    ) -> None:
        """Hands each message of the kinds to its handler, until cancelled, and calls
        ``subscribed`` each time the subscription is (again) in place, as what was sent while
        it was not is lost. Redis out of reach is a warning: it subscribes again, after a
        pause that grows up to ``RETRY_LAST``."""
        by_channel = {self._channel(kind).encode(): handle for kind, handle in handlers.items()}
        pause = RETRY_FIRST
        while True:
            try:
                async with self._redis.pubsub(ignore_subscribe_messages=True) as pubsub:
                    await pubsub.subscribe(*by_channel)
                    pause = RETRY_FIRST
                    subscribed()
                    while True:
                        message = await pubsub.get_message(timeout=READ_WAIT)
                        if message is not None:
                            _hand(by_channel, message["channel"], message["data"])
            except (RedisError, OSError) as error:
                logger.warning(
                    "The runner's messages are not heard (%s); trying again in %.1f s",
                    type(error).__name__,
                    pause,
                )
            await anyio.sleep(pause)
            pause = min(pause * 2, RETRY_LAST)


def _hand(
    handlers: Mapping[bytes, Callable[[list[str]], None]], channel: bytes, data: bytes
) -> None:
    """Hands the message's words to the handler of its channel; a handler that fails is a
    bug of ours (a Sentry issue), and the next message is heard still."""
    handle = handlers.get(channel)
    if handle is None:
        return
    try:
        handle(data.decode().split())
    except Exception:
        logger.exception("A message of the runner could not be handled")


class RemoteRunner:
    """The runner of another process, as an ``api`` process sees it: it asks the runner to
    fill the slots and to stop runs, and its waits hear what changed (``Watchers``) through
    one subscription (``start``) for the whole process."""

    def __init__(
        self,
        db: Database,
        jobs: JobClient,
        channel: RunnerChannel,
        *,
        slots: int,
        fill_wait: float = FILL_WAIT,
    ) -> None:
        self._db = db
        self._jobs = jobs
        self._channel = channel
        self._slots = slots
        self._fill_wait = fill_wait
        self._watchers = Watchers()
        # The fills asked for that the runner has not answered yet, by their token.
        self._asked: dict[str, asyncio.Event] = {}
        self._listening: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Listens to what the runner says, in a task of its own, until ``close``."""
        handlers = {CHANGED: self._changed, FILLED: self._filled}
        self._listening = asyncio.create_task(
            self._channel.listen(handlers), name="runner messages"
        )

    async def close(self) -> None:
        if self._listening is not None:
            self._listening.cancel()
            with suppress(asyncio.CancelledError):
                await self._listening
        await self._channel.close()

    async def ping(self) -> None:
        await self._jobs.ping()

    async def free_slot(self) -> bool:
        """Whether fewer backtests run than there are slots, as Postgres has them."""
        async with self._db.sessions() as session:
            return await BacktestStore(session).running_total() < self._slots

    async def fill(self) -> None:
        """Asks the runner to fill the slots and waits until it did, ``fill_wait`` at most:
        a runner that is away (restarting) fills them when it is back."""
        token = secrets.token_hex(8)
        answered = asyncio.Event()
        self._asked[token] = answered
        try:
            await self._channel.publish(WAKE, [token])
            with anyio.move_on_after(self._fill_wait):
                await answered.wait()
        finally:
            del self._asked[token]

    def stop(self, backtest_ids: Collection[str]) -> None:
        if not backtest_ids:
            return
        self._channel.send(STOP, backtest_ids)
        for backtest_id in backtest_ids:
            self.changed(backtest_id)

    def changed(self, backtest_id: str) -> None:
        """Tells the waits of this process at once, and those of the others."""
        self._watchers.changed(backtest_id)
        self._channel.send(CHANGED, [backtest_id])

    def watch(self, backtest_ids: Iterable[str]) -> AbstractContextManager[asyncio.Event]:
        return self._watchers.watch(backtest_ids)

    def _changed(self, backtest_ids: list[str]) -> None:
        for backtest_id in backtest_ids:
            self._watchers.changed(backtest_id)

    def _filled(self, tokens: list[str]) -> None:
        for token in tokens:
            answered = self._asked.get(token)
            if answered is not None:
                answered.set()
