"""What the API processes and the runner tell each other (``experiments.channel``), on fakes of
Redis: a word that cannot be sent is a warning, a subscription that fails subscribes again, a
start waits for the runner's fill a while at most, and the runner answers the wakes. Over a real
Redis and Postgres: tests/integration/experiments/test_runner_split.py."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from candlestack.experiments import BacktestRunner, RemoteRunner, RunnerChannel
from candlestack.experiments import channel as channel_module

pytestmark = pytest.mark.anyio


class FakeChannel:
    """Keeps what is sent instead of sending it."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, list[str]]] = []

    async def publish(self, kind: str, words: Any) -> None:
        self.sent.append((kind, list(words)))

    def send(self, kind: str, words: Any) -> None:
        self.sent.append((kind, list(words)))

    async def close(self) -> None:
        pass


class DownRedis:
    async def publish(self, *_args: Any) -> int:
        raise RedisConnectionError("Connection refused")


class FakePubSub:
    """Hands out ``messages`` one by one, then nothing."""

    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.messages = messages
        self.channels: tuple[Any, ...] = ()

    async def subscribe(self, *channels: Any) -> None:
        self.channels = channels

    async def get_message(self, timeout: float) -> dict[str, Any] | None:  # noqa: ASYNC109 (as redis-py)
        if self.messages:
            return self.messages.pop(0)
        await anyio.sleep(timeout)
        return None


class FlakyRedis:
    """A Redis whose first subscription fails, and whose second hands out ``messages``."""

    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.tries = 0
        self.pubsubs: list[FakePubSub] = []
        self._messages = messages

    @asynccontextmanager
    async def pubsub(self, **_options: Any) -> AsyncIterator[FakePubSub]:
        self.tries += 1
        if self.tries == 1:
            raise RedisConnectionError("Connection refused")
        pubsub = FakePubSub(self._messages)
        self.pubsubs.append(pubsub)
        yield pubsub


def remote(channel: Any, **options: Any) -> RemoteRunner:
    return RemoteRunner(None, None, channel, slots=2, **options)  # ty: ignore[invalid-argument-type]


async def test_a_word_that_cannot_be_sent_is_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    channel = RunnerChannel(DownRedis(), "test")  # ty: ignore[invalid-argument-type]

    await channel.publish("wake", ["token"])
    channel.send("changed", ["bt_7f3k2m9qx4"])
    await channel.close()

    assert "The runner's wake was not sent (ConnectionError)" in caplog.text
    assert "The runner's changed was not sent (ConnectionError)" in caplog.text


async def test_a_subscription_that_fails_subscribes_again_and_hears_the_words(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(channel_module, "RETRY_FIRST", 0.01)
    messages = [
        {"channel": b"test:stop", "data": b"bt_aaaaaaaaaa bt_bbbbbbbbbb"},
        {"channel": b"test:other", "data": b"bt_cccccccccc"},
        {"channel": b"test:wake", "data": b"token"},
    ]
    redis = FlakyRedis(messages)
    heard: list[tuple[str, list[str]]] = []
    subscribed = asyncio.Event()

    async with anyio.create_task_group() as group:
        group.start_soon(
            RunnerChannel(redis, "test").listen,  # ty: ignore[invalid-argument-type]
            {
                "stop": lambda words: heard.append(("stop", words)),
                "wake": lambda words: heard.append(("wake", words)),
            },
            subscribed.set,
        )
        with anyio.fail_after(5):
            await subscribed.wait()
            while len(heard) < 2:  # noqa: ASYNC110 (the fake tells nobody)
                await anyio.sleep(0.01)
        group.cancel_scope.cancel()

    assert "The runner's messages are not heard (ConnectionError)" in caplog.text
    assert redis.tries == 2
    assert redis.pubsubs[0].channels == (b"test:stop", b"test:wake")
    assert heard == [("stop", ["bt_aaaaaaaaaa", "bt_bbbbbbbbbb"]), ("wake", ["token"])]


async def test_a_word_whose_handling_fails_is_reported_and_the_next_is_heard(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fails(_words: list[str]) -> None:
        raise RuntimeError("boom")

    heard: list[list[str]] = []
    handlers = {b"test:stop": fails, b"test:wake": heard.append}

    with caplog.at_level(logging.ERROR):
        channel_module._hand(handlers, b"test:stop", b"bt_7f3k2m9qx4")
    channel_module._hand(handlers, b"test:wake", b"token")

    assert "A message of the runner could not be handled" in caplog.text
    assert heard == [["token"]]


async def test_a_fill_the_runner_does_not_answer_ends_after_its_wait() -> None:
    channel = FakeChannel()
    api = remote(channel, fill_wait=0.05)

    with anyio.fail_after(5):
        await api.fill()

    [(kind, [token])] = channel.sent
    assert kind == "wake"
    assert token
    assert api._asked == {}


async def test_a_fill_ends_once_the_runner_answers_its_token() -> None:
    channel = FakeChannel()
    api = remote(channel, fill_wait=30)

    async def answer() -> None:
        while not channel.sent:  # noqa: ASYNC110 (the fake tells nobody)
            await anyio.sleep(0.01)
        api._filled(["another", channel.sent[0][1][0]])

    async with anyio.create_task_group() as group:
        group.start_soon(answer)
        with anyio.fail_after(5):
            await api.fill()


async def test_a_stop_asks_the_runner_and_tells_the_waits_here() -> None:
    channel = FakeChannel()
    api = remote(channel)

    api.stop([])
    with api.watch(["bt_7f3k2m9qx4"]) as changed:
        api.stop(["bt_7f3k2m9qx4"])
        assert changed.is_set()

    assert channel.sent == [("stop", ["bt_7f3k2m9qx4"]), ("changed", ["bt_7f3k2m9qx4"])]


async def test_the_waits_of_an_api_process_hear_what_the_runner_says() -> None:
    api = remote(FakeChannel())

    with api.watch(["bt_aaaaaaaaaa"]) as changed, api.watch(["bt_bbbbbbbbbb"]) as other:
        api._changed(["bt_aaaaaaaaaa", "bt_cccccccccc"])

        assert (changed.is_set(), other.is_set()) == (True, False)


async def test_the_runner_answers_a_wake_once_its_fill_ended() -> None:
    channel = FakeChannel()
    runner = BacktestRunner(
        None,  # ty: ignore[invalid-argument-type]
        data=None,  # ty: ignore[invalid-argument-type]
        jobs=None,  # ty: ignore[invalid-argument-type]
        channel=channel,  # ty: ignore[invalid-argument-type]
    )
    filled: list[str] = []

    async def take_turn() -> str | None:
        filled.append("turn")
        return None

    runner._take_turn = take_turn  # ty: ignore[invalid-assignment]

    runner._woken_by(["one"])
    runner._woken_by(["two"])  # before the fill began: answered by the same fill
    await asyncio.gather(*runner._wakeups)
    runner.changed("bt_7f3k2m9qx4")
    await runner.close()

    assert filled == ["turn"]
    assert channel.sent == [("filled", ["one", "two"]), ("changed", ["bt_7f3k2m9qx4"])]
