"""Atomically update a bot token without printing it."""

import os
import re
import sys
import tempfile
from pathlib import Path


TOKEN_RE = re.compile(r"^[0-9]+:[A-Za-z0-9_-]+$")


def sync_token(env_path: Path, token: str, token_name: str = "TELEGRAM_BOT_TOKEN_PROD") -> None:
    if not TOKEN_RE.fullmatch(token):
        raise ValueError(f"{token_name} is missing or invalid")
    if not env_path.is_file() or env_path.is_symlink():
        raise FileNotFoundError("Bot .env must be an existing regular file")

    lines = env_path.read_text(encoding="utf-8").splitlines()
    updated: list[str] = []
    found = False
    for line in lines:
        if re.match(r"^\s*BOT_TOKEN\s*=", line):
            if not found:
                updated.append(f"BOT_TOKEN={token}")
                found = True
        else:
            updated.append(line)
    if not found:
        updated.insert(0, f"BOT_TOKEN={token}")

    if token_name == "TELEGRAM_BOT_TOKEN_DEV":
        # Never let a copied dev .env point at the production users or backups.
        updated = [
            line for line in updated
            if not re.match(r"^\s*(DATABASE_PATH|BACKUP_DIRECTORY)\s*=", line)
        ]
        updated.extend(["DATABASE_PATH=data/bot.sqlite3", "BACKUP_DIRECTORY=backups"])

    fd, temporary = tempfile.mkstemp(prefix=".env.", dir=env_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write("\n".join(updated) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, env_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit("Usage: sync_token.py /path/to/.env [TELEGRAM_BOT_TOKEN_PROD|TELEGRAM_BOT_TOKEN_DEV]")
    token_name = sys.argv[2] if len(sys.argv) == 3 else "TELEGRAM_BOT_TOKEN_PROD"
    if token_name not in {"TELEGRAM_BOT_TOKEN_PROD", "TELEGRAM_BOT_TOKEN_DEV"}:
        raise SystemExit("Unsupported token variable")
    sync_token(Path(sys.argv[1]), os.environ.get(token_name, ""), token_name)
    print("Bot token updated in .env")
