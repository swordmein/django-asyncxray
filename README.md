<p align="center">
  <img src="assets/asyncxray.png" alt="django-asyncxray logo" width="520">
</p>

<h1 align="center">django-asyncxray</h1>

<p align="center">
  <strong>See where Django crosses the sync/async boundary - and how long your code waits before it even starts running.</strong>
</p>

<p align="center">
  <a href="https://github.com/swordmein/django-asyncxray/actions/workflows/tests.yml">
    <img src="https://github.com/swordmein/django-asyncxray/actions/workflows/tests.yml/badge.svg" alt="tests">
  </a>
  <img src="https://img.shields.io/badge/status-alpha-orange" alt="status: alpha">
  <img src="https://img.shields.io/badge/Python-3.14%20validated-3776AB?logo=python&logoColor=white" alt="Python 3.14 validated">
  <img src="https://img.shields.io/badge/Django-6.1%20validated-092E20?logo=django&logoColor=white" alt="Django 6.1 validated">
  <img src="https://img.shields.io/badge/asgiref-3.12%20validated-4B5563" alt="asgiref 3.12 validated">
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT license">
  </a>
</p>

<p align="center">
  <em>Your Django code may be fast. The queue may not be.</em>
</p>

---

`django-asyncxray` is an experimental diagnostics library for Django applications that makes **sync/async boundary crossings** visible.

It instruments the places where Django and `asgiref` move work between synchronous and asynchronous execution, then separates the time you observe into the parts that matter:

```text
boundary time
├── queue wait       ← the work exists, but has not started running yet
└── worker execution ← the synchronous work is actually running
```

That distinction answers a question ordinary timing often cannot:

> **Is the code slow — or is fast code spending most of its life waiting for an execution lane?**

This is especially useful around thread-sensitive Django workloads, sync middleware inside ASGI applications, async ORM helpers, signals, and nested `sync_to_async` / `async_to_sync` transitions.

> [!WARNING]
> **Alpha software. Not production-ready yet.**
>
> The core measurement model is working and covered by an automated test suite, but the project still uses runtime instrumentation and monkey-patching of framework internals. Use it for development, diagnostics, experiments, and feedback — not as an always-on production agent yet.

## Why this exists

A request that takes `250 ms` does not necessarily contain `250 ms` of slow application code.

Consider five concurrent async tasks that each call a `50 ms` synchronous function through a thread-sensitive bridge:

```text
task 0  queue:   ~0 ms   execution: ~50 ms
task 1  queue:  ~50 ms   execution: ~50 ms
task 2  queue: ~100 ms   execution: ~50 ms
task 3  queue: ~150 ms   execution: ~50 ms
task 4  queue: ~200 ms   execution: ~50 ms
```

Every function is still approximately `50 ms`.

The bottleneck is the **serialized execution lane**, not the function itself.

`django-asyncxray` is being built to expose that difference directly.

## What it can see today

The current alpha captures and correlates:

| Area | What is captured |
| --- | --- |
| `sync_to_async` | boundary duration, task, loop, parent event, executor, queue wait, worker timing |
| `async_to_sync` | boundary duration and causal parent relationship |
| Thread-sensitive execution | shared executor identity and queue build-up |
| `ThreadSensitiveContext` | context identity and its executor lane |
| Django middleware adaptation | stable semantic causes such as `django.middleware:...` |
| Django signals | stable built-in names such as `django.signal:request_started` |
| Django response lifecycle | `django.response.close` |
| Async ORM helpers | semantic causes such as `django.orm:QuerySet.aget` and `django.orm:QuerySet.acreate` |
| Cancellation | caller cancellation time separated from continued synchronous worker execution |
| Trace storage | thread-safe bounded ring buffer with dropped-event accounting |

### Example semantic events

A real async Django request with a sync-only middleware can currently produce events such as:

```text
django.signal:request_started
        ↓
django.middleware:demoapp.middleware.LegacySyncMiddleware
        ↓
django.response.close
```

An async ORM call can be attributed to the synchronous operation it bridges into:

```text
django.orm:QuerySet.acreate
    → django.db.models.query.QuerySet.create

django.orm:QuerySet.aget
    → django.db.models.query.QuerySet.get
```

## Quick start

### Install for development

The project is currently in alpha development.

```bash
git clone https://github.com/swordmein/django-asyncxray.git
cd django-asyncxray

python -m venv .venv
source .venv/bin/activate

python -m pip install -e .
```

### Run the built-in self-test

```bash
asyncxray selftest
```

The self-test exercises four important execution patterns:

1. a single thread-sensitive sync bridge,
2. concurrent work serialized onto one thread-sensitive lane,
3. concurrent non-thread-sensitive work using a multi-worker pool,
4. an explicit `ThreadSensitiveContext`.

A representative contention result looks like:

```text
=== SELFTEST 2: 5 concurrent thread-sensitive bridges ===

Wall clock: ~253 ms

job 0  queue_wait=  ~0 ms   execution= ~50 ms
job 1  queue_wait= ~50 ms   execution= ~50 ms
job 2  queue_wait=~100 ms   execution= ~50 ms
job 3  queue_wait=~150 ms   execution= ~50 ms
job 4  queue_wait=~200 ms   execution= ~50 ms
```

The same five functions with `thread_sensitive=False` complete in roughly one `50 ms` window instead of roughly `250 ms`.

That contrast is the core diagnostic thesis of the project.

## Programmatic capture

The current alpha can also be used directly while the higher-level tracing CLI is still being built:

```python
import asyncio
import time

from asgiref.sync import sync_to_async

from asyncxray.capture.bridges import patch_all, unpatch_all
from asyncxray.model.events import trace_buffer


def blocking_work():
    time.sleep(0.05)
    return "done"


async def main():
    await sync_to_async(
        blocking_work,
        thread_sensitive=True,
    )()


patch_all()
trace_buffer.clear()

try:
    asyncio.run(main())

    for event in trace_buffer.all():
        print(
            event.direction,
            event.callable_id,
            event.queue_wait_ns,
            event.worker_execution_ns,
            event.semantic_cause,
        )
finally:
    unpatch_all()
```

`patch_all()` is intentionally guarded by runtime compatibility checks in the current alpha.

## The timing model

A boundary event records multiple clocks because **caller lifetime and worker lifetime are not always the same thing**.

### Queue wait

```text
executor.submit(...)
        │
        │ queue_wait
        ▼
worker starts
```

`queue_wait` answers:

> How long did this work wait after submission before an execution resource actually began running it?

### Worker execution

```text
worker starts
        │
        │ worker_execution
        ▼
worker finishes
```

This is the real synchronous execution interval when worker timing is available.

### Boundary duration

```text
caller enters bridge
        │
        │ boundary duration
        ▼
caller leaves bridge
```

Usually the boundary duration approximately includes queue wait plus execution.

Cancellation is the important exception.

For example, a caller may be cancelled after `~2 ms` while a synchronous worker continues for another `~200 ms`. `django-asyncxray` records those lifetimes separately instead of pretending the synchronous work ended when the caller stopped waiting.

## Semantic attribution

Raw bridge data is useful:

```text
sync_to_async
executor=ThreadPoolExecutor:...
queue_wait=151 ms
```

But diagnostics become much more useful when the bridge can answer **why it exists**:

```text
cause=django.middleware:demoapp.middleware.LegacySyncMiddleware
queue_wait=151 ms
```

The project currently attributes selected boundaries to semantic causes including:

```text
django.middleware:<dotted-path>
django.signal:request_started
django.response.close
django.orm:QuerySet.aget
django.orm:QuerySet.acreate
```

The long-term goal is to make traces read like Django architecture, not like implementation-detail stack dumps.

## Under the hood

The current implementation instruments selected framework boundaries including:

```text
asgiref.sync.SyncToAsync.__call__
asgiref.sync.AsyncToSync.__call__

asgiref.current_thread_executor.CurrentThreadExecutor.submit
concurrent.futures.ThreadPoolExecutor.submit

django.core.handlers.base.BaseHandler.adapt_method_mode
django.dispatch.Signal.send
django.dispatch.Signal.asend

selected django.db.models.query.QuerySet async methods
```

A `ContextVar` carries the active boundary and semantic cause across the relevant execution context. Events are stored in a thread-safe bounded ring buffer.

### Causal relationships

Nested bridge transitions preserve parent relationships:

```text
sync_to_async
└── async_to_sync
    └── sync_to_async
```

Each event can carry:

```text
event_id
parent_event_id
task_id
loop_id
executor_id
thread_sensitive_context_id
```

This is the foundation for future trace-tree and topology analysis.

## Thread-sensitive contention

One of the most important Django async failure modes is not technically a deadlock or a slow function. It is **unexpected serialization**.

A group of otherwise concurrent tasks may share:

```text
same ThreadSensitiveContext
        ↓
same executor
        ↓
single execution lane
        ↓
growing queue wait
```

`django-asyncxray` records the executor and thread-sensitive context identities needed to prove that relationship instead of inferring it only from wall-clock timing.

## Runtime compatibility

The instrumentation depends on framework internals, so the project deliberately fails closed on unvalidated runtime shapes.

### Currently validated

```text
Python   3.14.x
Django   6.1.x
asgiref  3.12.x
```

The runtime guard verifies both versions **and critical capabilities**, including the expected shape of:

```text
SyncToAsync.__call__
SyncToAsync.thread_handler
SyncToAsync.thread_sensitive_context
CurrentThreadExecutor.submit
BaseHandler.adapt_method_mode
Signal.send
Signal.asend
```

On an unsupported or incompatible runtime, patching is refused with an explicit error.

For development experiments only, the guard can be overridden:

```bash
ASYNCXRAY_ALLOW_UNSUPPORTED=1 asyncxray selftest
```

> [!CAUTION]
> The override means exactly what it says: unsupported. Measurements may be incomplete or incorrect.

## Safety and hardening

Instrumentation must not become the bug it is trying to diagnose.

The current test suite covers failure modes that are especially important for a tracer:

- idempotent `patch_all()` and `unpatch_all()`,
- complete restoration of patched framework methods,
- nested bridge causality,
- exception propagation and `ContextVar` cleanup,
- cancellation cleanup,
- worker completion after caller cancellation,
- thread-safe concurrent event writes,
- unique event IDs under load,
- bounded trace storage,
- queue timing integrity,
- worker start/finish timing integrity,
- real Django middleware attribution,
- real Django signal attribution,
- real async ORM attribution.

A development stress benchmark with `200` concurrent non-thread-sensitive bridges captured all `200` events with unique IDs and complete worker timing.

On the current development machine, the measured median tracing overhead in that microbenchmark was approximately:

```text
~1.5% total wall-clock overhead
~6 µs estimated overhead per boundary event
```

These numbers are **development measurements, not performance guarantees**. They are included to make the instrumentation cost visible and will be replaced by a repeatable benchmark suite as the project matures.

## Trace buffer

Long-running processes cannot safely keep every event forever.

The global trace buffer is therefore bounded:

```text
max_events = 10,000
```

Internally it uses a `collections.deque(maxlen=...)` ring buffer, so old events are evicted automatically once capacity is reached.

The buffer also tracks:

```python
trace_buffer.dropped_events
```

so a trace consumer can tell when the capture window was incomplete.

## Tests

Run the complete test suite with:

```bash
python -m pytest -q
```

The current alpha suite includes unit and integration coverage for bridge timing, contention, concurrency, patch lifecycle, Django semantics, ORM semantics, cancellation, compatibility guards, and ring-buffer behavior.

GitHub Actions runs the suite on the validated runtime.

## Project status

### Implemented

- [x] `SyncToAsync` boundary capture
- [x] `AsyncToSync` boundary capture
- [x] queue-wait measurement
- [x] worker start/finish timing
- [x] parent event relationships
- [x] task and event-loop identities
- [x] executor identity
- [x] `ThreadSensitiveContext` identity
- [x] middleware semantic attribution
- [x] request signal attribution
- [x] response-close attribution
- [x] selected async ORM attribution
- [x] cancellation-aware worker timing
- [x] bounded thread-safe trace buffer
- [x] runtime compatibility guard
- [x] automated unit and integration tests
- [x] CI workflow

### In progress / planned

- [ ] `asyncxray trace --url /...`
- [ ] request-scoped trace trees
- [ ] richer view attribution
- [ ] signal receiver attribution
- [ ] async iterator / ORM chunk-boundary attribution
- [ ] contention analysis and recommendations
- [ ] starvation and deadlock diagnostics
- [ ] Channels instrumentation
- [ ] NDJSON export
- [ ] Chrome Trace export
- [ ] OpenTelemetry export
- [ ] pytest assertions for hidden sync/async crossings
- [ ] multi-version adapter matrix
- [ ] stable public event schema
- [ ] production hardening

## Repository layout

```text
asyncxray/
├── capture/       # runtime instrumentation
├── model/         # events, trace state, schemas
├── analyze/       # contention/topology analysis (growing)
├── adapters/      # version-specific compatibility layer
├── exporters/     # trace export targets
├── replay/        # future replay/scheduling work
├── static/        # future static analysis
├── checks.py      # runtime compatibility guard
└── cli.py         # asyncxray CLI

testproject/       # real Django integration fixture
tests/
├── unit/
└── integration/
```

## Design principles

### Measure, do not guess

Queue wait is measured at submission and worker start. Worker execution is measured at worker start and finish.

### Preserve causality

A boundary is more useful when it can be connected to the boundary that caused it.

### Speak Django

Prefer:

```text
django.middleware:myproject.middleware.LegacyMiddleware
```

over an anonymous internal wrapper name whenever the framework gives enough information to recover the semantic cause.

### Fail closed

When an internal runtime shape has not been validated, refuse to instrument it by default.

### Keep observer effect visible

Tracing overhead, dropped events, cancellation behavior, and instrumentation limitations are part of the diagnostic model — not details to hide.

## What this project is not

`django-asyncxray` is not intended to replace a full APM, CPU profiler, SQL profiler, or distributed tracing platform.

It focuses on a narrower question:

> **What happens at Django's sync/async boundaries, what resource does the work wait for, and what semantic part of the framework caused that crossing?**

Those boundary-level facts can complement broader observability tools.

## Contributing

The project is early, and this is a good time to influence its event model and instrumentation strategy.

Useful contributions include:

- minimal reproductions of surprising Django async behavior,
- additional runtime compatibility research,
- tests for cancellation and nested bridges,
- Django middleware and ORM edge cases,
- benchmark methodology,
- semantic attribution improvements,
- exporter implementations,
- documentation corrections.

Development setup:

```bash
git clone https://github.com/swordmein/django-asyncxray.git
cd django-asyncxray

python -m venv .venv
source .venv/bin/activate

python -m pip install -e .
python -m pip install pytest pytest-asyncio pytest-django

python -m pytest -q
```

Please keep instrumentation changes covered by regression tests. A tracer that changes application behavior is worse than no tracer.

## Security

This project instruments framework internals and is not yet recommended for production deployment.

If you discover a security-sensitive issue, avoid publishing secrets, credentials, private traces, or production data in a public issue. Provide the smallest reproducible example possible.

## License

`django-asyncxray` is released under the [MIT License](LICENSE).

---

<p align="center">
  <strong>django-asyncxray</strong><br>
  <em>Make the hidden wait visible.</em>
</p>
