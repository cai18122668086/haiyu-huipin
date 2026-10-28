"""Bridge Celery's supported thread pool to one persistent asyncio runtime.

Celery retains delivery acknowledgements, inspection, result tracking and retry
semantics. Waiting tasks share one runner, database pool and asyncio primitives;
there is no new event loop/database engine for each delivery.
"""

import asyncio
import os
import threading
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from typing import Any


class WorkerAsyncRuntime:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pid: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def run[T](self, operation: Callable[[], Coroutine[Any, Any, T]]) -> T:
        with self._lock:
            if self._pid != os.getpid() or self._loop is None:
                loop = asyncio.new_event_loop()
                # Storage, reference packing and downloads use short blocking
                # operations. Provider HTTP itself uses native async sockets.
                loop.set_default_executor(
                    ThreadPoolExecutor(
                        max_workers=64,
                        thread_name_prefix="polaris-io",
                    )
                )
                ready = threading.Event()

                def serve() -> None:
                    asyncio.set_event_loop(loop)
                    ready.set()
                    loop.run_forever()

                thread = threading.Thread(
                    target=serve,
                    name="polaris-async",
                    daemon=True,
                )
                thread.start()
                ready.wait()
                self._pid, self._loop, self._thread = os.getpid(), loop, thread
            runtime_loop = self._loop

        async def invoke() -> T:
            return await operation()

        return asyncio.run_coroutine_threadsafe(invoke(), runtime_loop).result()

    def close(self) -> None:
        with self._lock:
            loop, thread = self._loop, self._thread
            if loop is None or self._pid != os.getpid():
                return

            async def finish() -> None:
                from .async_provider_transport import close_async_provider_session

                await close_async_provider_session()
                await loop.shutdown_asyncgens()
                await loop.shutdown_default_executor()

            asyncio.run_coroutine_threadsafe(finish(), loop).result()
            loop.call_soon_threadsafe(loop.stop)
            if thread is not None:
                thread.join()
            loop.close()
            self._loop = None
