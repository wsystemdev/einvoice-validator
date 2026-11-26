"""Run the e-invoice rule engine in separate worker processes with a hard wall-clock timeout (0.3.155.1, PG-1).

Why: SaxonC holds the GIL while it evaluates the Schematron XSLT, so a slow invoice run with asyncio.to_thread froze the
whole event loop (every endpoint, health checks included). A thread can't be stopped either. Here each validation goes to
a persistent worker process (python -m vatcheck.einvoice_worker) over a pipe; the caller's thread only waits on the pipe
(GIL released). On timeout the worker is killed and a fresh one is started.

Env: EINVOICE_WORKER=0 runs in-process (tests / local tools), EINVOICE_WORKERS (default 1, Render 512 MB: one warm worker
is about 250 MB and the API process no longer loads SaxonC), EINVOICE_TIMEOUT_S (default 20, per engine call),
EINVOICE_REQUEST_BUDGET_S (default 30, whole request incl. queue wait; set by the API per request), EINVOICE_QUEUE_WAIT_S
(default 15, max wait for a free worker).
Errors: ValidationTimeout (API: 422 validation_timeout), Busy (API: 503 busy + Retry-After), WorkerCrashed (API: 503).
"""
from __future__ import annotations
import contextvars, os, pickle, queue, select, struct, subprocess, sys, threading, time

TIMEOUT = float(os.getenv("EINVOICE_TIMEOUT_S", 20))
BUDGET = float(os.getenv("EINVOICE_REQUEST_BUDGET_S", 30))
QUEUE_WAIT = float(os.getenv("EINVOICE_QUEUE_WAIT_S", 15))
WORKERS = max(1, int(os.getenv("EINVOICE_WORKERS", 1)))
READY_WAIT = float(os.getenv("EINVOICE_WORKER_READY_S", 120))
IN_WORKER = os.getenv("_EINVOICE_IN_WORKER") == "1"

# Package-relative (0.3.157.1): the same file runs as vatcheck.einvoice_worker (API) and as wsystem_einvoice.engine.einvoice_worker
# (free self-hosted image, vendored unmodified by its scripts/sync_engine.py). _PKG_ROOT = directory that contains the top-level package.
_PKG = __spec__.parent if __spec__ and __spec__.parent else (__package__ or "vatcheck")
_PKG_ROOT = os.path.abspath(__file__)
for _ in range(len(_PKG.split(".")) + 1):
    _PKG_ROOT = os.path.dirname(_PKG_ROOT)

deadline: contextvars.ContextVar[float | None] = contextvars.ContextVar("einvoice_deadline", default=None)


class ValidationTimeout(Exception):
    """The rule engine did not finish within the time budget; the worker was killed."""


class Busy(Exception):
    """No worker became free within the queue wait / request budget."""


class WorkerCrashed(Exception):
    """The worker process died during the call (e.g. out of memory); it is restarted."""


def use_worker() -> bool:
    return not IN_WORKER and os.getenv("EINVOICE_WORKER", "1").lower() not in ("0", "false", "no", "off")


def begin_request(budget: float | None = None):
    """Set the per-request deadline (contextvar; copied into asyncio.to_thread). Returns a token for end_request."""
    return deadline.set(time.monotonic() + (BUDGET if budget is None else budget))


def end_request(token) -> None:
    try:
        deadline.reset(token)
    except Exception:
        pass


# ---------------------------------------------------------------- framing
def _send(f, obj) -> None:
    b = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    f.write(struct.pack(">Q", len(b)) + b)
    f.flush()


def _read_exact(fd: int, n: int, until: float | None) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        if until is not None:
            left = until - time.monotonic()
            if left <= 0:
                raise TimeoutError
            r, _, _ = select.select([fd], [], [], left)
            if not r:
                raise TimeoutError
        chunk = os.read(fd, min(n - len(buf), 1 << 20))
        if not chunk:
            raise EOFError
        buf += chunk
    return bytes(buf)


def _recv(fd: int, until: float | None):
    n = struct.unpack(">Q", _read_exact(fd, 8, until))[0]
    return pickle.loads(_read_exact(fd, n, until))


def _pack_exc(e: BaseException) -> dict:
    return {"module": type(e).__module__, "name": type(e).__name__, "args": [str(a) if not isinstance(a, (str, int, float)) else a for a in e.args],
            "dict": {k: v for k, v in vars(e).items() if isinstance(v, (str, int, float, bool, list, dict, type(None)))} if hasattr(e, "__dict__") else {}}


def _unpack_exc(d: dict) -> BaseException:
    cls = None
    if d["module"].startswith(_PKG + "."):
        try:
            import importlib
            cls = getattr(importlib.import_module(d["module"]), d["name"])
        except Exception:
            cls = None
    if cls is None or not (isinstance(cls, type) and issubclass(cls, BaseException)):
        cls = type(d["name"], (Exception,), {})  # e.g. PySaxonApiError: the API matches it by name, no SaxonC import here
    e = cls.__new__(cls)
    e.args = tuple(d["args"])
    for k, v in d["dict"].items():
        try:
            setattr(e, k, v)
        except Exception:
            pass
    return e


# ---------------------------------------------------------------- worker side
def _serve() -> None:
    out = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)  # stray prints (SaxonC, libraries) go to stderr, never into the protocol stream
    inp = sys.stdin.buffer
    from . import einvoice
    einvoice.warm_local()
    _send(out, ("ready", os.getpid()))
    while True:
        try:
            n = inp.read(8)
            if len(n) < 8:
                return
            fn, args = pickle.loads(inp.read(struct.unpack(">Q", n)[0]))
        except Exception:
            return
        try:
            res = ("ok", getattr(einvoice, fn)(*args))
        except BaseException as e:  # noqa: BLE001 - everything goes back to the caller
            res = ("err", _pack_exc(e))
        try:
            _send(out, res)
        except Exception as e:
            _send(out, ("err", _pack_exc(RuntimeError(f"result could not be sent: {e}"))))


# ---------------------------------------------------------------- API side
class _Worker:
    def __init__(self):
        self.p: subprocess.Popen | None = None
        self.ready = False

    def start(self) -> None:
        env = dict(os.environ, _EINVOICE_IN_WORKER="1", EINVOICE_WORKER="0")
        self.p = subprocess.Popen([sys.executable, "-m", _PKG + ".einvoice_worker"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=None, env=env, cwd=_PKG_ROOT, close_fds=True)
        self.ready, self.started = False, time.monotonic()

    def alive(self) -> bool:
        return self.p is not None and self.p.poll() is None

    def kill(self) -> None:
        if self.p is not None:
            try:
                self.p.kill()
                self.p.wait(5)
            except Exception:
                pass
            for f in (self.p.stdin, self.p.stdout):
                try:
                    f.close()
                except Exception:
                    pass
        self.p, self.ready = None, False

    def restart(self) -> None:
        self.kill()
        self.start()

    def call(self, fn: str, args: tuple, dl: float):
        if not self.alive():
            self.start()
        fd = self.p.stdout.fileno()
        if not self.ready:  # first call after a (re)start: the worker compiles the rule sets (a few seconds)
            try:
                msg = _recv(fd, min(dl, self.started + READY_WAIT))
            except TimeoutError:
                if time.monotonic() - self.started >= READY_WAIT:
                    self.restart()
                    raise WorkerCrashed("the validation worker did not start")
                raise Busy("the validation worker is starting")
            except (EOFError, OSError):
                self.restart()
                raise WorkerCrashed("the validation worker did not start")
            self.ready = msg[0] == "ready"
        until = min(time.monotonic() + TIMEOUT, dl)
        if until - time.monotonic() <= 0.05:
            raise ValidationTimeout("validation took too long and was stopped")
        try:
            _send(self.p.stdin, (fn, args))
            st, val = _recv(fd, until)
        except TimeoutError:
            self.restart()
            raise ValidationTimeout("validation took too long and was stopped")
        except (EOFError, OSError, BrokenPipeError):
            self.restart()
            raise WorkerCrashed("the validation worker stopped during the check")
        if st == "ok":
            return val
        raise _unpack_exc(val)


_lock = threading.Lock()
_pool: queue.Queue | None = None
_all: list[_Worker] = []


def start() -> None:
    """Create and start the workers (idempotent). Called at API startup; call() starts them lazily too."""
    global _pool
    with _lock:
        if _pool is not None:
            return
        q = queue.Queue()
        for _ in range(WORKERS):
            w = _Worker()
            w.start()
            _all.append(w)
            q.put(w)
        _pool = q


def stop() -> None:
    global _pool
    with _lock:
        for w in _all:
            w.kill()
        _all.clear()
        _pool = None


def call(fn: str, *args):
    """Run einvoice.<fn>(*args) in a worker. Blocks the calling thread (not the GIL) for at most the request deadline."""
    if _pool is None:
        start()
    now = time.monotonic()
    dl = deadline.get() or (now + BUDGET)
    try:
        w = _pool.get(timeout=max(0.0, min(QUEUE_WAIT, dl - now)))
    except queue.Empty:
        raise Busy("all validation workers are busy")
    try:
        return w.call(fn, args, dl)
    finally:
        _pool.put(w)


def http_error(e: BaseException):
    """(status, body, headers) for the PG-1 errors (caps, timeout, busy, crashed), else None. Nothing is billed for these."""
    from . import einvoice
    nb = {}
    if isinstance(e, einvoice.TooLarge):
        b = {"error": "payload_too_large", "error_code": e.code, "message": str(e), "detail": str(e)}
        if e.limit is not None:
            b["limit"] = e.limit
        if e.found is not None:
            b["found"] = e.found
        return 413, b, dict(nb, **{"X-No-Bill": e.code})
    if isinstance(e, ValidationTimeout):
        m = (f"Validation took longer than the time limit and was stopped. Very large or error-heavy invoices can hit it: "
             f"fix the first errors or split the invoice (max {einvoice.MAX_LINES} lines).")
        return 422, {"error": "validation_timeout", "error_code": "validation_timeout", "message": m, "detail": m}, dict(nb, **{"X-No-Bill": "validation_timeout"})
    if isinstance(e, (Busy, WorkerCrashed)):
        m = "The validator is busy. Retry in a few seconds. Nothing was charged."
        return 503, {"error": "busy", "error_code": "validator_busy", "message": m, "detail": m}, dict(nb, **{"X-No-Bill": "busy", "Retry-After": "5"})
    return None


if __name__ == "__main__":
    _serve()
