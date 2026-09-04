import asyncio

from requirement_agent.application.ingestion.tasks import run_worker_coroutine


async def current_loop() -> asyncio.AbstractEventLoop:
    return asyncio.get_running_loop()


def test_worker_reuses_event_loop_between_tasks() -> None:
    first = run_worker_coroutine(current_loop())
    second = run_worker_coroutine(current_loop())

    assert first is second
    assert not first.is_closed()
