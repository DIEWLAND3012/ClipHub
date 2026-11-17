"""Job queue: runs BaseMediaTasks on TaskWorker threads, several at once when the hardware allows.

Each video-encoding job is given its own encoder engine (an NVIDIA GPU, an Intel/AMD/Apple GPU or
the CPU), so e.g. one conversion runs on the GPU while another runs on the CPU. Jobs that do not
encode video (downloads) run alongside, and subtitle jobs take the Whisper slot one at a time.
"""
from __future__ import annotations

from collections import Counter, deque
from collections.abc import Callable, Iterable

from PySide6.QtCore import QObject, Signal

from core.base_task import BaseMediaTask
from core.hwaccel import Engine, cpu_engine, parallel_limit, pick_engine
from core.worker import TaskWorker

WHISPER = "whisper"


class TaskQueue(QObject):
    task_added = Signal(object)               # task
    task_started = Signal(object)             # task
    task_progress = Signal(object, int, str)  # task, percent, message
    task_done = Signal(object, object)        # task, output Path
    task_failed = Signal(object, str)         # task, message
    task_cancelled = Signal(object)           # task

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pending: deque[BaseMediaTask] = deque()
        self._running: dict[TaskWorker, BaseMediaTask] = {}
        self._engines: Callable[[], Iterable[Engine]] = lambda: [cpu_engine()]
        self._limit: Callable[[], int] = lambda: 0
        self._use_gpu: Callable[[], bool] = lambda: True

    def configure(self, engines: Callable[[], Iterable[Engine]], limit: Callable[[], int],
                  use_gpu: Callable[[], bool]) -> None:
        """Where to read the available engines and the user's performance settings."""
        self._engines, self._limit, self._use_gpu = engines, limit, use_gpu
        self._start_next()

    def enqueue(self, task: BaseMediaTask) -> None:
        self._pending.append(task)
        self.task_added.emit(task)
        self._start_next()

    def cancel(self, task: BaseMediaTask) -> None:
        worker = self._worker_for(task)
        if worker is not None:
            worker.cancel()  # task_cancelled fires when the worker returns
        elif task in self._pending:
            self._pending.remove(task)
            self.task_cancelled.emit(task)

    def is_busy(self) -> bool:
        return bool(self._running)

    def running_tasks(self) -> list[BaseMediaTask]:
        return list(self._running.values())

    def pending_count(self) -> int:
        return len(self._pending)

    def shutdown(self, timeout_ms: int = 5000) -> None:
        """Drop pending jobs, cancel the running ones and wait for their threads. Call on app exit."""
        self._pending.clear()
        for worker in list(self._running):
            worker.cancel()
        for worker in list(self._running):
            worker.wait(timeout_ms)

    # --- scheduling -------------------------------------------------------------------
    def _worker_for(self, task: BaseMediaTask) -> TaskWorker | None:
        return next((w for w, t in self._running.items() if t is task), None)

    def _busy(self) -> Counter:
        busy: Counter = Counter()
        for task in self._running.values():
            if getattr(task, "engine", None) is not None:
                busy[task.engine.key] += 1
            if task.resource() == WHISPER:
                busy[WHISPER] += 1
        return busy

    def _start_next(self) -> None:
        engines = list(self._engines())
        limit = parallel_limit(engines, self._limit())
        started = True
        while started and self._pending and len(self._running) < limit:
            started = False
            busy = self._busy()
            for task in list(self._pending):
                need = task.resource()
                if need == WHISPER:
                    if busy[WHISPER]:
                        continue
                elif need is not None:
                    engine = pick_engine(need, engines, busy, self._use_gpu())
                    if engine is None:
                        continue  # every engine for this codec is busy; a later job may still fit
                    task.engine = engine
                self._pending.remove(task)
                self._start(task)
                started = True
                break

    def _start(self, task: BaseMediaTask) -> None:
        worker = TaskWorker(task, self)
        worker.progress.connect(lambda percent, message, t=task: self.task_progress.emit(t, percent, message))
        worker.finished_ok.connect(lambda output, t=task: self.task_done.emit(t, output))
        worker.failed.connect(lambda message, t=task: self.task_failed.emit(t, message))
        worker.cancelled.connect(lambda t=task: self.task_cancelled.emit(t))
        worker.finished.connect(lambda w=worker: self._on_worker_finished(w))
        self._running[worker] = task
        self.task_started.emit(task)
        worker.start()

    def _on_worker_finished(self, worker: TaskWorker) -> None:
        self._running.pop(worker, None)
        worker.wait()  # finished is emitted from the thread itself; let it fully stop first
        worker.deleteLater()
        self._start_next()
