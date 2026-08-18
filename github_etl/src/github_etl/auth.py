from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _load_dotenv() -> None:
    package_root = Path(__file__).resolve().parents[2]
    load_dotenv(package_root / ".env")
    load_dotenv()


def _token_from_gh_cli() -> str | None:
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    token = result.stdout.strip()
    return token or None


@dataclass(frozen=True)
class GitHubAuth:
    token: str

    @classmethod
    def from_env(cls) -> GitHubAuth:
        _load_dotenv()
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not token:
            token = _token_from_gh_cli()
        if not token:
            raise RuntimeError(
                "No GitHub token found. Set GITHUB_TOKEN in github_etl/.env "
                "(see .env.example) or log in with `gh auth login`."
            )
        return cls(token=token)

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def verify_login(self, timeout_s: float = 20.0) -> str:
        """Return the authenticated GitHub login, or raise."""
        import httpx

        query = "query { viewer { login } }"
        response = httpx.post(
            "https://api.github.com/graphql",
            headers=self.headers(),
            json={"query": query},
            timeout=timeout_s,
        )
        response.raise_for_status()
        payload = response.json()
        errors = payload.get("errors")
        if errors:
            raise RuntimeError(f"GitHub GraphQL auth failed: {errors}")
        login = payload["data"]["viewer"]["login"]
        if not login:
            raise RuntimeError("GitHub GraphQL returned an empty viewer login.")
        return login
