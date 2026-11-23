import base64
import json

import pytest
import requests

from src.user_store import ConflictError, GitHubBackend, MemoryBackend, StoreError, UserStore


class FakeResponse:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {}

    def json(self):
        return self._body


class FakeHttp:
    """Records calls; each get/put pops the next queued response (or raises it if exception)."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def _next(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url, **kwargs):
        return self._next("GET", url, kwargs)

    def put(self, url, **kwargs):
        return self._next("PUT", url, kwargs)


def file_body(doc, sha="abc123"):
    return {"encoding": "base64", "content": base64.b64encode(json.dumps(doc).encode()).decode(), "sha": sha}


def backend(*responses):
    http = FakeHttp(*responses)
    return GitHubBackend("tok", repo="o/r", branch="user-data", http=http), http


# ---- GitHubBackend.read ----------------------------------------------------------------


def test_read_decodes_document_and_returns_sha():
    b, http = backend(FakeResponse(200, file_body({"pins": []}, sha="s1")))
    assert b.read("pins.json") == ({"pins": []}, "s1")
    method, url, kwargs = http.calls[0]
    assert url == "https://api.github.com/repos/o/r/contents/pins.json"
    assert kwargs["params"] == {"ref": "user-data"}
    assert kwargs["headers"]["Authorization"] == "Bearer tok"


def test_read_missing_file_is_none_not_error():
    b, _ = backend(FakeResponse(404, {"message": "Not Found"}))
    assert b.read("pins.json") == (None, None)


def test_read_missing_branch_is_an_error_not_an_empty_file():
    b, _ = backend(FakeResponse(404, {"message": "No commit found for the ref user-data"}))
    with pytest.raises(StoreError, match="does not exist"):
        b.read("pins.json")


@pytest.mark.parametrize("status", [401, 403])
def test_read_auth_failure_is_a_clear_store_error(status):
    b, _ = backend(FakeResponse(status, {"message": "Bad credentials"}))
    with pytest.raises(StoreError, match="rejected the user-data token"):
        b.read("pins.json")


def test_read_network_failure_is_a_store_error():
    b, _ = backend(requests.ConnectionError("boom"))
    with pytest.raises(StoreError, match="unreachable"):
        b.read("pins.json")


def test_read_corrupt_json_is_a_store_error():
    body = {"encoding": "base64", "content": base64.b64encode(b"{not json").decode(), "sha": "s"}
    b, _ = backend(FakeResponse(200, body))
    with pytest.raises(StoreError, match="not valid JSON"):
        b.read("pins.json")


# ---- GitHubBackend.write ---------------------------------------------------------------


def test_write_create_omits_sha_and_targets_the_branch():
    b, http = backend(FakeResponse(201))
    b.write("pins.json", {"pins": []}, None, "pin CPB")
    _, _, kwargs = http.calls[0]
    payload = kwargs["json"]
    assert "sha" not in payload
    assert payload["branch"] == "user-data"
    assert payload["message"] == "[user-data] pin CPB"
    assert json.loads(base64.b64decode(payload["content"])) == {"pins": []}


def test_write_update_sends_sha():
    b, http = backend(FakeResponse(200))
    b.write("pins.json", {"pins": []}, "s1", "m")
    assert http.calls[0][2]["json"]["sha"] == "s1"


@pytest.mark.parametrize("status", [409, 422])
def test_write_stale_sha_is_a_conflict(status):
    b, _ = backend(FakeResponse(status, {"message": "does not match"}))
    with pytest.raises(ConflictError):
        b.write("pins.json", {}, "old", "m")


def test_write_auth_failure_is_not_a_conflict():
    b, _ = backend(FakeResponse(403, {"message": "Resource not accessible"}))
    with pytest.raises(StoreError) as excinfo:
        b.write("pins.json", {}, None, "m")
    assert not isinstance(excinfo.value, ConflictError)


# ---- UserStore.update ------------------------------------------------------------------


def default():
    return {"n": 0}


def test_update_creates_missing_file_from_default():
    mem = MemoryBackend()
    store = UserStore(mem)
    result = store.update("f.json", lambda d: {**d, "n": d["n"] + 1}, default, "bump")
    assert result == {"n": 1}
    assert mem.files["f.json"][0] == {"n": 1}
    assert mem.writes == [("f.json", "[user-data] bump")]


def test_update_noop_makes_no_commit():
    mem = MemoryBackend()
    store = UserStore(mem)
    assert store.update("f.json", lambda d: d, default, "nothing") == {"n": 0}
    assert mem.writes == [] and "f.json" not in mem.files


def test_update_retries_once_on_conflict_and_reapplies_to_fresh_doc():
    mem = MemoryBackend()
    store = UserStore(mem)
    store.update("f.json", lambda d: {**d, "n": 10}, default, "seed")
    calls = {"count": 0}

    def bump(d):
        calls["count"] += 1
        if calls["count"] == 1:  # someone else writes between our read and our write
            mem.write("f.json", {"n": 100}, str(mem.files["f.json"][1]), "other writer")
        return {**d, "n": d["n"] + 1}

    result = store.update("f.json", bump, default, "bump")
    assert calls["count"] == 2
    assert result == {"n": 101}  # re-applied on the *fresh* document, not replayed over the stale one
    assert mem.files["f.json"][0] == {"n": 101}


def test_update_gives_up_after_retries():
    mem = MemoryBackend()
    store = UserStore(mem)
    store.update("f.json", lambda d: {**d, "n": 1}, default, "seed")

    def always_clash(d):
        mem.write("f.json", {"n": d["n"] + 1000}, str(mem.files["f.json"][1]), "other writer")
        return {**d, "n": d["n"] + 1}

    with pytest.raises(ConflictError):
        store.update("f.json", always_clash, default, "bump", retries=1)


def test_update_does_not_mutate_the_stored_document_on_failure():
    mem = MemoryBackend()
    store = UserStore(mem)
    store.update("f.json", lambda d: {**d, "n": 5}, default, "seed")
    mem.fail_next_write = StoreError("down")
    with pytest.raises(StoreError):
        store.update("f.json", lambda d: {**d, "n": 6}, default, "bump")
    assert store.read("f.json", default) == {"n": 5}
