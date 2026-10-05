"""Worker-local SQL/HTTP/SDK privacy; unrelated operations keep their telemetry."""

import logging
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import ParamSpec, TypeVar

import sentry_sdk

_private: ContextVar[bool] = ContextVar("private_referral_delivery", default=False)
_installed = False
P = ParamSpec("P")
T = TypeVar("T")


@contextmanager
def private_scope() -> Iterator[None]:
    global _installed
    if not _installed:
        original = logging.getLogRecordFactory()

        def factory(*args: object, **kwargs: object) -> logging.LogRecord:
            record = original(*args, **kwargs)
            if _private.get():
                record.msg = "Private referral delivery operation"
                record.args = ()
                record.exc_info = None
                record.exc_text = None
                record.stack_info = None
            return record

        logging.setLogRecordFactory(factory)
        _installed = True
    token = _private.set(True)
    try:
        with sentry_sdk.isolation_scope() as scope:
            scope.clear_breadcrumbs()
            scope.add_event_processor(lambda _event, _hint: None)
            yield
    finally:
        _private.reset(token)


def private_operation(
    function: Callable[P, Coroutine[object, object, T]],
) -> Callable[P, Coroutine[object, object, T]]:
    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        with private_scope():
            return await function(*args, **kwargs)

    return wrapped
