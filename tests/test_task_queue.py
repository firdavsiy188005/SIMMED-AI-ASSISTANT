# -*- coding: utf-8 -*-
import threading
import time

import pytest

from agent.task_queue import TaskQueue, TaskPriority, TaskStatus


def wait_until(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


class FakeExecutor:
    def __init__(self, gate=None, fail_on=(), result="done"):
        self.gate = gate
        self.fail_on = set(fail_on)
        self.result = result
        self.order = []
        self.running = 0
        self.max_running = 0
        self._lock = threading.Lock()

    def execute(self, goal, speak=None, cancel_flag=None):
        with self._lock:
            self.order.append(goal)
            self.running += 1
            self.max_running = max(self.max_running, self.running)
        try:
            if self.gate is not None:
                self.gate.wait(5)
            if goal in self.fail_on:
                raise RuntimeError(f"boom:{goal}")
            return f"{self.result}:{goal}"
        finally:
            with self._lock:
                self.running -= 1


@pytest.fixture
def make_queue():
    queues = []

    def _make(executor, max_concurrent=1, start=True):
        q = TaskQueue(max_concurrent=max_concurrent)
        q._executor = executor
        if start:
            q.start()
        queues.append(q)
        return q

    yield _make
    for q in queues:
        q.stop()


def test_task_completes_and_callback_fires(make_queue):
    ex = FakeExecutor()
    q = make_queue(ex)
    got = []
    tid = q.submit("goal A", on_complete=lambda i, r: got.append((i, r)))
    assert wait_until(lambda: q.get_status(tid)["status"] == "completed")
    assert q.get_status(tid)["result"] == "done:goal A"
    assert wait_until(lambda: got == [(tid, "done:goal A")])


def test_failed_task_records_error(make_queue):
    q = make_queue(FakeExecutor(fail_on={"bad"}))
    tid = q.submit("bad")
    assert wait_until(lambda: q.get_status(tid)["status"] == "failed")
    assert "boom:bad" in q.get_status(tid)["error"]


def test_queue_keeps_working_after_failure(make_queue):
    q = make_queue(FakeExecutor(fail_on={"bad"}))
    q.submit("bad")
    tid = q.submit("good")
    assert wait_until(lambda: q.get_status(tid)["status"] == "completed")


def test_callback_exception_does_not_kill_queue(make_queue):
    q = make_queue(FakeExecutor())

    def bad_cb(*_):
        raise ValueError("cb fail")

    t1 = q.submit("one", on_complete=bad_cb)
    t2 = q.submit("two")
    assert wait_until(lambda: q.get_status(t2)["status"] == "completed")
    assert q.get_status(t1)["status"] == "completed"


def test_priority_order(make_queue):
    ex = FakeExecutor()
    q = make_queue(ex, start=False)
    q.submit("low", priority=TaskPriority.LOW)
    q.submit("normal")
    q.submit("high", priority=TaskPriority.HIGH)
    q.start()
    assert wait_until(lambda: len(ex.order) == 3)
    assert ex.order == ["high", "normal", "low"]


def test_fifo_within_same_priority(make_queue):
    ex = FakeExecutor()
    q = make_queue(ex, start=False)
    for i in range(5):
        q.submit(f"g{i}")
        time.sleep(0.002)
    q.start()
    assert wait_until(lambda: len(ex.order) == 5)
    assert ex.order == [f"g{i}" for i in range(5)]


def test_max_concurrent_is_respected(make_queue):
    gate = threading.Event()
    ex = FakeExecutor(gate=gate)
    q = make_queue(ex, max_concurrent=2)
    ids = [q.submit(f"g{i}") for i in range(5)]
    assert wait_until(lambda: ex.running == 2)
    time.sleep(0.2)
    assert ex.running == 2
    gate.set()
    assert wait_until(lambda: all(q.get_status(i)["status"] == "completed" for i in ids))
    assert ex.max_running == 2


def test_cancel_pending_task_never_runs(make_queue):
    gate = threading.Event()
    ex = FakeExecutor(gate=gate)
    q = make_queue(ex)
    first = q.submit("first")
    assert wait_until(lambda: ex.running == 1)
    second = q.submit("second")
    assert q.cancel(second) is True
    gate.set()
    assert wait_until(lambda: q.get_status(first)["status"] == "completed")
    time.sleep(0.2)
    assert "second" not in ex.order
    assert q.get_status(second)["status"] == "cancelled"


def test_cancel_running_task_sets_flag_and_skips_callback(make_queue):
    gate = threading.Event()
    ex = FakeExecutor(gate=gate)
    q = make_queue(ex)
    got = []
    tid = q.submit("x", on_complete=lambda *a: got.append(a))
    assert wait_until(lambda: ex.running == 1)
    assert q.cancel(tid)
    assert q._tasks[tid].cancel_flag.is_set()
    gate.set()
    time.sleep(0.2)
    assert q.get_status(tid)["status"] == "cancelled"
    assert got == []


def test_cancel_unknown_or_finished(make_queue):
    q = make_queue(FakeExecutor())
    assert q.cancel("nope") is False
    tid = q.submit("x")
    assert wait_until(lambda: q.get_status(tid)["status"] == "completed")
    assert q.cancel(tid) is False


def test_cancelled_tasks_are_removed_from_internal_queue(make_queue):
    """Cancelled pending tasks stay in ``_queue`` forever (they are skipped but
    never removed), so the list grows for the lifetime of the app."""
    gate = threading.Event()
    q = make_queue(FakeExecutor(gate=gate))
    q.submit("blocker")
    time.sleep(0.1)
    for i in range(20):
        q.cancel(q.submit(f"c{i}"))
    gate.set()
    time.sleep(0.2)
    assert len(q._queue) == 0


def test_status_helpers(make_queue):
    gate = threading.Event()
    q = make_queue(FakeExecutor(gate=gate))
    q.submit("a" * 80)
    q.submit("b")
    time.sleep(0.1)
    assert q.pending_count() == 1
    all_s = q.get_all_statuses()
    assert len(all_s) == 2 and len(all_s[0]["goal"]) == 50
    assert q.get_status("missing") is None
    gate.set()


def test_start_twice_and_stop(make_queue):
    q = make_queue(FakeExecutor())
    t = q._worker_thread
    q.start()
    assert q._worker_thread is t
    q.stop()
    t.join(3)
    assert not t.is_alive()


def test_task_ordering_dataclass():
    from agent.task_queue import Task
    a = Task(priority=1, created_at=2, task_id="a", goal="")
    b = Task(priority=2, created_at=1, task_id="b", goal="")
    assert a < b
    assert TaskStatus.PENDING.value == "pending"
