import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from .paths import app_data_dir

# Per-file size + backup count for the rotating log handler. Defaults
# bound the on-disk footprint to ~20 MB (5 MB × 1 active + 3 backups).
# Generous enough that a single bug-storm session lands in one file but
# tight enough that long-running desktop installs don't accrete GBs of
# logs over months.
_LOG_MAX_BYTES = 5 * 1024 * 1024
_LOG_BACKUP_COUNT = 3


# User data directory for logs, config, and data
def get_user_data_dir() -> str:
    """Returns the user data directory for the application.

    Honors ``ENMOTION_DATA_DIR`` for tests and managed installations. The normal
    location is the OS application-data directory, which is deliberately
    independent from every earlier product namespace.
    """
    return str(app_data_dir())


def get_log_dir() -> str:
    """Returns the log directory.

    Honors the ENMOTION_LOG_DIR environment variable when set; otherwise
    defaults to <user_data_dir>/logs.
    """
    env_log_dir = os.environ.get("ENMOTION_LOG_DIR", "").strip()
    log_dir = (
        os.path.expanduser(env_log_dir)
        if env_log_dir
        else os.path.join(get_user_data_dir(), "logs")
    )
    os.makedirs(log_dir, exist_ok=True)
    return log_dir


def _setup_warning(message: str) -> None:
    stream = sys.stderr or sys.__stderr__
    if stream is not None:
        print(message, file=stream)


def setup_logging(level=logging.INFO, log_file=None):
    """Configure one bounded file sink and, when available, one console sink."""
    root_logger = logging.getLogger()
    if any(getattr(handler, "_enmotion_handler", False) for handler in root_logger.handlers):
        root_logger.setLevel(level)
        return

    handlers = []

    # If no log file specified, use default in user directory
    if log_file is None:
        try:
            log_file = os.path.join(get_log_dir(), "app.log")
        except OSError as exc:
            _setup_warning(
                f"WARNING: Log directory unavailable in user home: {exc}. "
                "Falling back to console logging."
            )
            log_file = None

    # 如果指定了日志文件，添加文件处理器
    if log_file:
        try:
            # 确保日志目录存在
            log_dir = os.path.dirname(log_file)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)

            file_handler = RotatingFileHandler(
                log_file,
                mode="a",
                maxBytes=_LOG_MAX_BYTES,
                backupCount=_LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setFormatter(
                logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
            )
            handlers.append(file_handler)
        except OSError as exc:
            # In restricted environments (tests/sandbox), fallback to console-only logging.
            _setup_warning(
                f"WARNING: File logging unavailable at '{log_file}': {exc}. "
                "Falling back to console logging."
            )

    # 添加控制台处理器（会被重定向到日志文件）
    if sys.stdout is not None:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        handlers.append(console_handler)

    for handler in handlers:
        handler._enmotion_handler = True
        root_logger.addHandler(handler)
    root_logger.setLevel(level)


def get_logger(name):
    """Returns a logger with the specified name."""
    return logging.getLogger(name)
