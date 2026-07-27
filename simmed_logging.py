# -*- coding: utf-8 -*-
"""SIMMED markaziy loglash tizimi.

main.py eng boshida setup_logging() ni chaqiradi — shundan keyin istalgan modul
oddiy `logging.getLogger(__name__)` bilan yozsa, hammasi logs/simmed.log ga tushadi.
PyInstaller exe'da ham ishlaydi (fayl exe yonidagi logs/ papkaga yoziladi).
"""
import logging
import logging.handlers
import sys
import threading
from pathlib import Path

LOG_DIR_NAME = "logs"
LOG_FILE_NAME = "simmed.log"
MAX_BYTES = 2 * 1024 * 1024  # 2 MB
BACKUP_COUNT = 3


def _log_dir() -> Path:
    # PyInstaller exe: log exe yonida; oddiy ishga tushirish: loyiha ildizida
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).parent
    d = base / LOG_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Root loggerni sozlaydi: aylanma fayl + konsol. Qayta chaqirish xavfsiz."""
    root = logging.getLogger()
    if getattr(root, "_simmed_configured", False):
        return root
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        file_handler = logging.handlers.RotatingFileHandler(
            _log_dir() / LOG_FILE_NAME,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError:
        # Disk/ruxsat muammosi bo'lsa ham dastur ishlashda davom etsin
        pass

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.setLevel(logging.WARNING)
    root.addHandler(console)

    # Ushlanmagan xatolar ham logga tushsin (asosiy thread + boshqa threadlar)
    def _excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        root.critical("USHLANMAGAN XATO", exc_info=(exc_type, exc_value, exc_tb))

    sys.excepthook = _excepthook

    def _thread_excepthook(args):
        if issubclass(args.exc_type, SystemExit):
            return
        root.critical(
            "USHLANMAGAN XATO (thread: %s)", args.thread.name if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = _thread_excepthook

    # Shovqinli kutubxonalarni jimlatish
    for noisy in ("httpx", "httpcore", "urllib3", "websockets", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    root._simmed_configured = True
    root.info("=== SIMMED loglash tizimi ishga tushdi ===")
    return root
