"""
core/logger.py — Sistema de logging centralizado do Paraíso Bot
Suporta logging para ficheiro (rotativo) e para a consola com cores por nível.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional
import threading


# ---------------------------------------------------------------------------
# Cores ANSI para a consola (funciona no Windows 10+ e em terminais Unix)
# ---------------------------------------------------------------------------
class _AnsiColors:
    RESET   = "\033[0m"
    DEBUG   = "\033[36m"    # Ciano
    INFO    = "\033[32m"    # Verde
    WARNING = "\033[33m"    # Amarelo
    ERROR   = "\033[31m"    # Vermelho
    CRITICAL= "\033[35m"    # Magenta


class ColoredFormatter(logging.Formatter):
    """Formatter que adiciona cor ao nível de log na consola."""

    LEVEL_COLORS = {
        logging.DEBUG:    _AnsiColors.DEBUG,
        logging.INFO:     _AnsiColors.INFO,
        logging.WARNING:  _AnsiColors.WARNING,
        logging.ERROR:    _AnsiColors.ERROR,
        logging.CRITICAL: _AnsiColors.CRITICAL,
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self.LEVEL_COLORS.get(record.levelno, _AnsiColors.RESET)
        record.levelname = f"{color}{record.levelname:8s}{_AnsiColors.RESET}"
        return super().format(record)


# ---------------------------------------------------------------------------
# Factory de loggers — mantém uma instância por módulo
# ---------------------------------------------------------------------------
_loggers: dict[str, logging.Logger] = {}
_lock = threading.Lock()


def get_logger(
    name: str,
    log_dir: Optional[Path] = None,
    level: str = "DEBUG",
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
) -> logging.Logger:
    """
    Retorna (ou cria) um logger com o nome indicado.

    Parâmetros:
        name         : Nome do módulo (ex: 'pms_service', 'whatsapp_service')
        log_dir      : Diretório onde guardar os ficheiros de log.
                       Se None, tenta usar config.LOG_DIR; se ainda assim
                       não estiver disponível, apenas loga para a consola.
        level        : Nível mínimo de log ('DEBUG', 'INFO', etc.)
        max_bytes    : Tamanho máximo do ficheiro de log antes de rodar
        backup_count : Número de ficheiros de backup a manter
    """
    with _lock:
        if name in _loggers:
            return _loggers[name]

        logger = logging.getLogger(name)
        logger.setLevel(getattr(logging, level.upper(), logging.DEBUG))
        logger.propagate = False  # Evitar duplicação com o root logger

        fmt_file    = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        fmt_console = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
        date_fmt    = "%Y-%m-%d %H:%M:%S"

        # ---- Handler de consola com cores --------------------------------
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(
            ColoredFormatter(fmt_console, datefmt=date_fmt)
        )
        logger.addHandler(console_handler)

        # ---- Handler de ficheiro rotativo --------------------------------
        resolved_log_dir: Optional[Path] = log_dir

        if resolved_log_dir is None:
            try:
                from paraiso_bot import config as _cfg
                resolved_log_dir = _cfg.LOG_DIR
            except ImportError:
                resolved_log_dir = None

        if resolved_log_dir is not None:
            resolved_log_dir.mkdir(parents=True, exist_ok=True)
            log_file = resolved_log_dir / f"{name}.log"
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            file_handler.setFormatter(
                logging.Formatter(fmt_file, datefmt=date_fmt)
            )
            logger.addHandler(file_handler)

        _loggers[name] = logger
        return logger


# ---------------------------------------------------------------------------
# Logger raiz da aplicação — usado quando não se especifica módulo
# ---------------------------------------------------------------------------
def get_app_logger() -> logging.Logger:
    """Retorna o logger principal da aplicação (nome: 'paraiso_bot')."""
    try:
        from paraiso_bot import config as _cfg
        return get_logger("paraiso_bot", log_dir=_cfg.LOG_DIR, level=_cfg.LOG_LEVEL)
    except ImportError:
        return get_logger("paraiso_bot", level="DEBUG")


# ---------------------------------------------------------------------------
# Conveniência: log de atividade estruturado (para guardar na BD depois)
# ---------------------------------------------------------------------------
class ActivityLogger:
    """
    Wrapper que emite logs Python E prepara entradas para a tabela
    activity_log da base de dados (sem acesso direto à BD aqui — evitar
    dependência circular; a BD processa a fila separadamente).
    """

    def __init__(self, module_name: str):
        self._logger = get_logger(module_name)
        self._module = module_name
        self._queue: list[dict] = []
        self._queue_lock = threading.Lock()

    def info(self, message: str, details: str = "") -> None:
        self._logger.info(message)
        self._enqueue("INFO", message, details)

    def warning(self, message: str, details: str = "") -> None:
        self._logger.warning(message)
        self._enqueue("WARNING", message, details)

    def error(self, message: str, details: str = "") -> None:
        self._logger.error(message)
        self._enqueue("ERROR", message, details)

    def critical(self, message: str, details: str = "") -> None:
        self._logger.critical(message)
        self._enqueue("CRITICAL", message, details)

    def debug(self, message: str, details: str = "") -> None:
        self._logger.debug(message)
        # Mensagens DEBUG não são persistidas na BD (demasiado verboso)

    def _enqueue(self, level: str, message: str, details: str) -> None:
        """Adiciona entrada à fila para persistência futura na BD."""
        with self._queue_lock:
            self._queue.append({
                "level":   level,
                "module":  self._module,
                "message": message,
                "details": details,
            })

    def flush_queue(self) -> list[dict]:
        """Retorna e limpa a fila de entradas pendentes (para o db_manager processar)."""
        with self._queue_lock:
            entries = list(self._queue)
            self._queue.clear()
        return entries
