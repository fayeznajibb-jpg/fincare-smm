import logging
import re
import os
from datetime import datetime


class SecureLogger:
    """
    Logger that automatically masks sensitive data before writing to logs.
    Prevents API keys, tokens, and secrets from appearing in log files.
    """

    # Patterns that match sensitive credentials — always masked
    SENSITIVE_PATTERNS = [
        r'sk-ant-[a-zA-Z0-9\-_]{20,}',          # Anthropic API key
        r'\d{8,12}:[a-zA-Z0-9_\-]{30,}',         # Telegram bot token
        r'AQV[a-zA-Z0-9\-_]{20,}',               # LinkedIn access token
        r'Bearer [a-zA-Z0-9\-_.]{20,}',           # Bearer tokens
        r'access_token=[a-zA-Z0-9\-_.%]{20,}',   # URL access tokens
        r'client_secret=[a-zA-Z0-9\-_.]{10,}',   # Client secrets
    ]

    def __init__(self, name: str):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.INFO)

        # Prevent duplicate handlers if logger already exists
        if self.logger.handlers:
            return

        os.makedirs('logs', exist_ok=True)
        log_filename = f'logs/fincare_smm_{datetime.now().strftime("%Y%m%d")}.log'

        file_handler = logging.FileHandler(log_filename, encoding='utf-8')
        console_handler = logging.StreamHandler()

        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)

        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)

    def _mask(self, message: str) -> str:
        """Replace sensitive patterns with [REDACTED]"""
        text = str(message)
        for pattern in self.SENSITIVE_PATTERNS:
            text = re.sub(pattern, '[REDACTED]', text)
        return text

    def info(self, message: str):
        self.logger.info(self._mask(message))

    def error(self, message: str):
        self.logger.error(self._mask(message))

    def warning(self, message: str):
        self.logger.warning(self._mask(message))

    def debug(self, message: str):
        self.logger.debug(self._mask(message))

    def success(self, message: str):
        self.logger.info(f"✅ {self._mask(message)}")

    def step(self, message: str):
        self.logger.info(f"→ {self._mask(message)}")
