"""
Structured logging module for Biotact API.

Provides JSON-formatted logging with context support.
Based on Python stdlib logging + structlog for structured output.
"""

import logging
import sys
from typing import Any

# ═══════════════════════════════════════════════════════════════════
# Logger Configuration
# ═══════════════════════════════════════════════════════════════════


def setup_logging(log_level: str = "INFO", json_logs: bool = True) -> None:
    """
    Configure application-wide logging.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_logs: If True, output JSON-formatted logs (for production)
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    # Configure root logger
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        if not json_logs
        else "%(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    # Silence noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


# ═══════════════════════════════════════════════════════════════════
# Structured Logger
# ═══════════════════════════════════════════════════════════════════


class StructuredLogger:
    """
    Structured logger with context support.

    Example:
        logger = get_logger(__name__)
        logger.info("User logged in", extra={"user_id": 123, "ip": "1.2.3.4"})
    """

    def __init__(self, name: str):
        self.logger = logging.getLogger(name)

    def _log_with_context(
        self, level: int, msg: str, extra: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        """Internal method to log with structured context."""
        if extra:
            # Add context as extra fields
            self.logger.log(level, msg, extra=extra, **kwargs)
        else:
            self.logger.log(level, msg, **kwargs)

    def debug(self, msg: str, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        """Log debug message with optional context."""
        self._log_with_context(logging.DEBUG, msg, extra, **kwargs)

    def info(self, msg: str, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        """Log info message with optional context."""
        self._log_with_context(logging.INFO, msg, extra, **kwargs)

    def warning(self, msg: str, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        """Log warning message with optional context."""
        self._log_with_context(logging.WARNING, msg, extra, **kwargs)

    def error(self, msg: str, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        """Log error message with optional context."""
        self._log_with_context(logging.ERROR, msg, extra, **kwargs)

    def critical(self, msg: str, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        """Log critical message with optional context."""
        self._log_with_context(logging.CRITICAL, msg, extra, **kwargs)

    def exception(self, msg: str, extra: dict[str, Any] | None = None, **kwargs: Any) -> None:
        """Log exception with traceback and optional context."""
        if extra:
            self.logger.exception(msg, extra=extra, **kwargs)
        else:
            self.logger.exception(msg, **kwargs)


# ═══════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════


def get_logger(name: str) -> StructuredLogger:
    """
    Get a structured logger instance.

    Args:
        name: Logger name (usually __name__)

    Returns:
        StructuredLogger instance

    Example:
        from src.common.logger import get_logger

        logger = get_logger(__name__)
        logger.info("Processing payment", extra={
            "user_id": user.id,
            "amount": payment.amount,
            "currency": "UZS"
        })
    """
    return StructuredLogger(name)


# ═══════════════════════════════════════════════════════════════════
# Usage Examples
# ═══════════════════════════════════════════════════════════════════

"""
# In main.py:
from src.common.logger import setup_logging, get_logger

setup_logging(log_level="INFO", json_logs=True)
logger = get_logger(__name__)

# In any module:
from src.common.logger import get_logger

logger = get_logger(__name__)

# Simple logging:
logger.info("Application started")
logger.error("Failed to connect to database")

# Structured logging with context:
logger.info("User created", extra={
    "user_id": user.id,
    "email": user.email,
    "role": user.role
})

logger.error("Payment failed", extra={
    "user_id": user.id,
    "order_id": order.id,
    "amount": payment.amount,
    "error_code": "INSUFFICIENT_FUNDS"
})

# Exception logging:
try:
    result = process_payment(order)
except Exception as e:
    logger.exception(
        "Payment processing failed",
        extra={"order_id": order.id, "user_id": user.id}
    )
    raise
"""
