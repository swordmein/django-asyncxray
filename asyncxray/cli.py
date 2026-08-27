from __future__ import annotations

import argparse

from asyncxray.capture.bridges import patch_all
from asyncxray.model.events import trace_buffer


def _print_events(events) -> None:
    for ev in events:
        dur = ev.duration_ms
        dur_str = f"{dur:.2f} ms" if dur is not None else "?"

        qw = ev.queue_wait_ns
        qw_ms = qw / 1_000_000 if qw is not None else None
        qw_str = f"{qw_ms:.2f} ms" if qw_ms is not None else "-"

        execution_ms = (
            dur - qw_ms
            if dur is not None and qw_ms is not None
            else None
        )
        execution_str = (
            f"{execution_ms:.2f} ms"
            if execution_ms is not None
            else "-"
        )

        parent_str = str(ev.parent_event_id) if ev.parent_event_id is not None else "-"
        task_str = hex(ev.task_id) if ev.task_id is not None else "-"
        loop_str = hex(ev.loop_id) if ev.loop_id is not None else "-"
        executor_str = ev.executor_id if ev.executor_id is not None else "-"
        tsctx_str = (
            ev.thread_sensitive_context_id
            if ev.thread_sensitive_context_id is not None
            else "-"
        )
        cause_str = ev.semantic_cause if ev.semantic_cause is not None else "-"

        print(
            f"[{ev.direction:15s}] {ev.callable_id:50s} "
            f"total={dur_str:>10s}  "
            f"queue_wait={qw_str:>10s}  "
            f"execution={execution_str:>10s}  "
            f"parent={parent_str:>4s}  "
            f"task={task_str:>14s}  "
            f"loop={loop_str:>14s}  "
            f"executor={executor_str}  "
            f"tsctx={tsctx_str}  "
            f"cause={cause_str}"
        )


def cmd_selftest(args: argparse.Namespace) -> None:
    import asyncio
    import time

    from asgiref.sync import ThreadSensitiveContext, async_to_sync, sync_to_async

    patch_all()

    # ------------------------------------------------------------
    # Test 1: single bridge baseline
    # ------------------------------------------------------------

    def slow_sync_function():
        time.sleep(0.05)
        return "done"

    async def run_single():
        return await sync_to_async(
            slow_sync_function,
            thread_sensitive=True,
        )()

    before = len(trace_buffer.all())

    async_to_sync(run_single)()

    single_events = trace_buffer.all()[before:]

    print("\n=== SELFTEST 1: single thread-sensitive bridge ===\n")
    print(f"Event count: {len(single_events)}\n")
    _print_events(single_events)

    # ------------------------------------------------------------
    # Test 2: contention on one thread-sensitive execution lane
    # ------------------------------------------------------------

    def contended_sync_function(job_id: int):
        time.sleep(0.05)
        return job_id

    async def run_contention():
        tasks = [
            sync_to_async(
                contended_sync_function,
                thread_sensitive=True,
            )(job_id)
            for job_id in range(5)
        ]

        started = time.perf_counter()
        results = await asyncio.gather(*tasks)
        elapsed = time.perf_counter() - started

        return results, elapsed

    async def run_parallel():
        tasks = [
            sync_to_async(
                contended_sync_function,
                thread_sensitive=False,
            )(job_id)
            for job_id in range(5)
        ]

        started = time.perf_counter()
        results = await asyncio.gather(*tasks)
        elapsed = time.perf_counter() - started

        return results, elapsed


    async def run_thread_sensitive_context():
        async with ThreadSensitiveContext():
            tasks = [
                sync_to_async(
                    contended_sync_function,
                    thread_sensitive=True,
                )(job_id)
                for job_id in range(5)
            ]

            started = time.perf_counter()
            results = await asyncio.gather(*tasks)
            elapsed = time.perf_counter() - started

            return results, elapsed

    before = len(trace_buffer.all())

    results, elapsed = async_to_sync(run_contention)()

    contention_events = trace_buffer.all()[before:]

    print("\n=== SELFTEST 2: 5 concurrent thread-sensitive bridges ===\n")
    print(f"Results: {results}")
    print(f"Wall clock: {elapsed * 1000:.2f} ms")
    print(f"Event count: {len(contention_events)}\n")

    _print_events(contention_events)

    sync_events = [
        ev
        for ev in contention_events
        if ev.direction == "sync_to_async"
        and "contended_sync_function" in ev.callable_id
    ]

    waits_ms = [
        ev.queue_wait_ns / 1_000_000
        for ev in sync_events
        if ev.queue_wait_ns is not None
    ]

    print("\nQueue waits:")
    for index, wait_ms in enumerate(waits_ms):
        print(f"  job {index}: {wait_ms:8.2f} ms")

    if waits_ms:
        print(f"\nMin queue wait: {min(waits_ms):.2f} ms")
        print(f"Max queue wait: {max(waits_ms):.2f} ms")

    print("\nExpected pattern:")
    print("  job 0: ~0 ms")
    print("  job 1: ~50 ms")
    print("  job 2: ~100 ms")
    print("  job 3: ~150 ms")
    print("  job 4: ~200 ms")
    print("  wall clock: ~250 ms")
    before = len(trace_buffer.all())

    results, elapsed = async_to_sync(run_parallel)()

    parallel_events = trace_buffer.all()[before:]

    print("\n=== SELFTEST 3: 5 concurrent NON-thread-sensitive bridges ===\n")
    print(f"Results: {results}")
    print(f"Wall clock: {elapsed * 1000:.2f} ms")
    print(f"Event count: {len(parallel_events)}\n")

    _print_events(parallel_events)
        # ------------------------------------------------------------
    # Test 4: explicit ThreadSensitiveContext
    # ------------------------------------------------------------

    before = len(trace_buffer.all())

    results, tsctx_elapsed = asyncio.run(run_thread_sensitive_context())

    tsctx_events = trace_buffer.all()[before:]

    print("\n=== SELFTEST 4: explicit ThreadSensitiveContext ===\n")
    print(f"Results: {results}")
    print(f"Wall clock: {tsctx_elapsed * 1000:.2f} ms")
    print(f"Event count: {len(tsctx_events)}\n")

    _print_events(tsctx_events)

    tsctx_sync = [
        ev
        for ev in tsctx_events
        if ev.direction == "sync_to_async"
        and "contended_sync_function" in ev.callable_id
    ]

    tsctx_waits_ms = [
        ev.queue_wait_ns / 1_000_000
        for ev in tsctx_sync
        if ev.queue_wait_ns is not None
    ]

    print("\nThreadSensitiveContext queue waits:")
    for index, wait_ms in enumerate(tsctx_waits_ms):
        print(f"  job {index}: {wait_ms:8.2f} ms")

    # ------------------------------------------------------------
    # Assertions / self-validation
    # ------------------------------------------------------------

    print("\n=== SELFTEST VALIDATION ===\n")

    failures: list[str] = []

    # Test 1: baseline bridge should have measurable queue wait.
    single_sync = [
        ev
        for ev in single_events
        if ev.direction == "sync_to_async"
    ]

    if len(single_sync) != 1:
        failures.append(
            f"single test expected 1 sync_to_async event, got {len(single_sync)}"
        )
    elif single_sync[0].queue_wait_ns is None:
        failures.append(
            "single thread-sensitive bridge has no queue_wait measurement"
        )

    # Test 2: contention should serialize approximately one 50 ms unit at a time.
    contention_sync = [
        ev
        for ev in contention_events
        if ev.direction == "sync_to_async"
        and "contended_sync_function" in ev.callable_id
    ]

    contention_waits_ms = [
        ev.queue_wait_ns / 1_000_000
        for ev in contention_sync
        if ev.queue_wait_ns is not None
    ]

    if len(contention_sync) != 5:
        failures.append(
            f"contention test expected 5 sync_to_async events, "
            f"got {len(contention_sync)}"
        )

    if len(contention_waits_ms) != 5:
        failures.append(
            "contention test did not measure queue_wait for all 5 jobs"
        )
    else:
        # Avoid brittle exact timing assertions.
        # We only care that serialization clearly builds up.
        if contention_waits_ms[-1] < 150:
            failures.append(
                "thread-sensitive contention did not produce substantial queue buildup"
            )

        if not all(
            later > earlier + 25
            for earlier, later in zip(
                contention_waits_ms,
                contention_waits_ms[1:],
            )
        ):
            failures.append(
                "thread-sensitive queue waits are not increasing as expected"
            )

    if elapsed > 0.120:
        failures.append(
            f"non-thread-sensitive workload unexpectedly slow: "
            f"{elapsed * 1000:.2f} ms"
        )

    # Test 3: parallel jobs should all have queue visibility,
    # but should not accumulate large serialization delays.
    parallel_sync = [
        ev
        for ev in parallel_events
        if ev.direction == "sync_to_async"
        and "contended_sync_function" in ev.callable_id
    ]

    parallel_waits_ms = [
        ev.queue_wait_ns / 1_000_000
        for ev in parallel_sync
        if ev.queue_wait_ns is not None
    ]

    if len(parallel_sync) != 5:
        failures.append(
            f"parallel test expected 5 sync_to_async events, "
            f"got {len(parallel_sync)}"
        )

    if len(parallel_waits_ms) != 5:
        failures.append(
            "parallel test did not measure queue_wait for all 5 jobs"
        )
    elif max(parallel_waits_ms) > 25:
        failures.append(
            f"parallel workload unexpectedly queued for "
            f"{max(parallel_waits_ms):.2f} ms"
        )

    # Test 4: explicit ThreadSensitiveContext should map all five
    # bridges to one context and one single-worker ThreadPoolExecutor.
    if len(tsctx_sync) != 5:
        failures.append(
            f"ThreadSensitiveContext test expected 5 sync_to_async events, "
            f"got {len(tsctx_sync)}"
        )
    else:
        context_ids = {
            ev.thread_sensitive_context_id
            for ev in tsctx_sync
            if ev.thread_sensitive_context_id is not None
        }
        executor_ids = {
            ev.executor_id
            for ev in tsctx_sync
            if ev.executor_id is not None
        }

        if len(context_ids) != 1:
            failures.append(
                f"ThreadSensitiveContext test expected exactly 1 context id, "
                f"got {len(context_ids)}"
            )

        if len(executor_ids) != 1:
            failures.append(
                f"ThreadSensitiveContext test expected exactly 1 executor id, "
                f"got {len(executor_ids)}"
            )

        if len(tsctx_waits_ms) != 5:
            failures.append(
                "ThreadSensitiveContext test did not measure all queue waits"
            )
        elif tsctx_waits_ms[-1] < 150:
            failures.append(
                "ThreadSensitiveContext did not produce expected serialization"
            )

    # AsyncToSync's event-loop ThreadPoolExecutor must not be mistaken
    # for SyncToAsync worker queue wait.
    async_to_sync_events = [
        ev
        for ev in single_events + contention_events + parallel_events
        if ev.direction == "async_to_sync"
    ]

    contaminated = [
        ev
        for ev in async_to_sync_events
        if ev.queue_wait_ns is not None
    ]

    if contaminated:
        failures.append(
            f"{len(contaminated)} async_to_sync event(s) incorrectly received queue_wait"
        )

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"  - {failure}")
        raise SystemExit(1)

    print("PASS")
    print("  ✓ baseline queue timing")
    print("  ✓ thread-sensitive serialization detected")
    print("  ✓ non-thread-sensitive parallel execution detected")
    print("  ✓ executor attribution is direction-safe")
    print("  ✓ ThreadSensitiveContext lane attribution")


def main() -> None:
    parser = argparse.ArgumentParser(prog="asyncxray")
    sub = parser.add_subparsers(dest="command", required=True)

    selftest_parser = sub.add_parser("selftest")
    selftest_parser.set_defaults(func=cmd_selftest)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
