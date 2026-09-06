from creasy.azure.client import _is_http, _ssh_to_https
from creasy.workspace.gitops import inject_token


def test_azure_token_uses_pat_user_not_oauth2():
    url = "https://ado.example/tfs/DefaultCollection/App/_git/app"
    got = inject_token(url, "secret-pat", scheme="azure")
    assert "pat:secret-pat@" in got
    assert "oauth2:" not in got
    gitlab = inject_token(url, "secret-pat")
    assert "oauth2:secret-pat@" in gitlab
    assert inject_token(url, "") == url


def test_ssh_remote_converts_to_https():
    assert _is_http("https://ado.example/col/App/_git/app")
    assert not _is_http("ssh://ado.example/col/App/_git/app")
    assert _ssh_to_https("git@ado.example:tfs/DefaultCollection/App/_git/app") == (
        "https://ado.example/tfs/DefaultCollection/App/_git/app"
    )
    assert _ssh_to_https("ssh://git@ado.example/tfs/DefaultCollection/App/_git/app") == (
        "https://ado.example/tfs/DefaultCollection/App/_git/app"
    )