import base64

from creasy.azure.auth import azure_basic_auth
from creasy.azure.client import _is_git_http, _is_http, _ssh_to_https
from creasy.workspace.gitops import inject_token, isolated_git_env


def test_azure_token_uses_pat_user_not_oauth2():
    url = "https://ado.example/tfs/DefaultCollection/App/_git/app"
    got = inject_token(url, "secret-pat", scheme="azure")
    assert "pat:secret-pat@" in got
    assert "oauth2:" not in got
    gitlab = inject_token(url, "secret-pat")
    assert "oauth2:secret-pat@" in gitlab
    assert inject_token(url, "") == url


def test_azure_basic_auth_is_not_empty_username():
    header = azure_basic_auth("secret-pat")
    decoded = base64.b64decode(header.split(" ", 1)[1]).decode("ascii")
    assert decoded == "pat:secret-pat"
    assert not decoded.startswith(":")


def test_inject_token_encodes_pat_special_chars():
    url = "https://tfs02.company.com.tr/tfs/ExampleCollection/Example%20Projeleri/_git/ProjectX"
    got = inject_token(url, "ab+c/d=", scheme="azure")
    assert "pat:ab%2Bc%2Fd%3D@" in got


def test_azure_git_env_sends_basic_header():
    env = isolated_git_env("secret-pat", auth_scheme="azure")
    assert env["GIT_CONFIG_KEY_0"] == "http.extraHeader"
    assert env["GIT_CONFIG_VALUE_0"].startswith("Authorization: Basic ")
    decoded = base64.b64decode(env["GIT_CONFIG_VALUE_0"].split(" ", 2)[2]).decode("ascii")
    assert decoded == "pat:secret-pat"
    gitlab = isolated_git_env("secret-pat")
    assert "GIT_CONFIG_KEY_0" not in gitlab


def test_api_url_is_not_a_git_clone_url():
    assert _is_http("https://tfs02.company.com.tr/tfs/ExampleCollection/_apis/git/repositories/x")
    assert not _is_git_http("https://tfs02.company.com.tr/tfs/ExampleCollection/_apis/git/repositories/x")
    assert _is_git_http("https://tfs02.company.com.tr/tfs/ExampleCollection/Example%20Projeleri/_git/ProjectX")


def test_ssh_remote_converts_to_https():
    assert _is_http("https://ado.example/col/App/_git/app")
    assert not _is_http("ssh://ado.example/col/App/_git/app")
    assert _ssh_to_https("git@ado.example:tfs/DefaultCollection/App/_git/app") == (
        "https://ado.example/tfs/DefaultCollection/App/_git/app"
    )
    assert _ssh_to_https("ssh://git@ado.example/tfs/DefaultCollection/App/_git/app") == (
        "https://ado.example/tfs/DefaultCollection/App/_git/app"
    )