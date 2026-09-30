"""Atomically update the production bot token without printing either token."""

import os
import re
import sys
import tempfile
from pathlib import Path


TOKEN_RE = re.compile(r"^[0-9]+:[A-Za-z0-9_-]+$")


def sync_token(env_path: Path, token: str) -> None:
    if not TOKEN_RE.fullmatch(token):
        raise ValueError("TELEGRAM_BOT_TOKEN_PROD is missing or invalid")
    if not env_path.is_file() or env_path.is_symlink():
        raise FileNotFoundError("Production .env must be an existing regular file")

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
    if len(sys.argv) != 2:
        raise SystemExit("Usage: sync_token.py /path/to/.env")
    sync_token(Path(sys.argv[1]), os.environ.get("TELEGRAM_BOT_TOKEN_PROD", ""))
    print("Production bot token updated in .env")
