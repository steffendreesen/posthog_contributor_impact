from github_etl.auth import GitHubAuth


def test_headers_use_bearer_token() -> None:
    auth = GitHubAuth(token="test-token")
    assert auth.headers()["Authorization"] == "Bearer test-token"
