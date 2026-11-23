"""The ONLY place in the app that writes to GitHub (docs/SPEC.md section 8, "Write ownership").

Two writers, never sharing a target: scripts/refresh_job.py (GitHub Actions) writes results/,
state/ and watchlists/ on `main`; the app writes user-authored data (pins today; match
feedback/notes and others later) to the dedicated `user-data` branch through this module.
That keeps user writes instant, conflict-free against the hourly `[data]` commits, and unable
to trigger a Streamlit redeploy (the app only redeploys on pushes to `main`).

Everything here is storage plumbing and knows nothing about pins: a "collection" is just a
JSON document at a path on the branch. A new kind of write = a new path + a small module like
src/pins.py that supplies the pure "doc -> new doc" function; no new GitHub code.

Planned layout on the branch (only pins.json exists today):
    pins.json                      global pinned wall
    data/<setup>/<ticker>.json     future: per-ticker feedback/notes thread (path carries the
                                   setup and ticker, so the file does not repeat them)
"""

import base64
import json
from typing import Callable

import requests

USER_DATA_REPO = "developerof1/SwingDeck"
USER_DATA_BRANCH = "user-data"
TOKEN_SECRET_NAME = "GITHUB_USERDATA_TOKEN"
HTTP_TIMEOUT_SECONDS = 8
COMMIT_PREFIX = "[user-data]"

# Set only by tests (see tests/test_user_store.py): lets AppTest drive app.py against an
# in-memory backend without any token or network. app.py checks this before st.secrets.
TEST_BACKEND = None


class StoreError(Exception):
    """Anything that stops a read/write from completing (network, auth, missing branch)."""


class ConflictError(StoreError):
    """The file changed between our read and our write (GitHub 409/422 on a stale sha)."""


def _commit_message(message: str) -> str:
    return f"{COMMIT_PREFIX} {message}"


class GitHubBackend:
    """Reads/writes JSON documents on one branch via GitHub's contents API.

    Reads deliberately go through the API (with the token), not raw.githubusercontent.com -
    raw is CDN-cached for minutes, which would break read-after-write. The API also works if
    the repo is ever made private.
    """

    API = "https://api.github.com"

    def __init__(
        self,
        token: str,
        repo: str = USER_DATA_REPO,
        branch: str = USER_DATA_BRANCH,
        timeout: float = HTTP_TIMEOUT_SECONDS,
        http=requests,
    ):
        self._token = token
        self._repo = repo
        self._branch = branch
        self._timeout = timeout
        self._http = http

    def _url(self, path: str) -> str:
        return f"{self.API}/repos/{self._repo}/contents/{path}"

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    @staticmethod
    def _message(resp) -> str:
        try:
            return str(resp.json().get("message", ""))
        except ValueError:
            return ""

    def read(self, path: str) -> tuple[dict | None, str | None]:
        """Returns (document, sha); (None, None) if the file doesn't exist yet."""
        try:
            resp = self._http.get(
                self._url(path), headers=self._headers(), params={"ref": self._branch}, timeout=self._timeout
            )
        except requests.RequestException as exc:
            raise StoreError(f"GitHub unreachable: {exc}") from exc

        if resp.status_code == 404:
            if "No commit found" in self._message(resp):
                raise StoreError(f"Branch '{self._branch}' does not exist in {self._repo}.")
            return None, None
        if resp.status_code in (401, 403):
            raise StoreError("GitHub rejected the user-data token (expired, revoked, or missing Contents access).")
        if resp.status_code != 200:
            raise StoreError(f"GitHub read failed ({resp.status_code}): {self._message(resp)}")

        body = resp.json()
        if body.get("encoding") != "base64":
            raise StoreError(f"Unexpected encoding for {path}: {body.get('encoding')!r}")
        try:
            return json.loads(base64.b64decode(body["content"])), body["sha"]
        except (ValueError, KeyError) as exc:
            raise StoreError(f"{path} is not valid JSON: {exc}") from exc

    def write(self, path: str, doc: dict, sha: str | None, message: str) -> None:
        payload = {
            "message": _commit_message(message),
            "content": base64.b64encode((json.dumps(doc, indent=2) + "\n").encode()).decode(),
            "branch": self._branch,
        }
        if sha:
            payload["sha"] = sha
        try:
            resp = self._http.put(self._url(path), headers=self._headers(), json=payload, timeout=self._timeout)
        except requests.RequestException as exc:
            raise StoreError(f"GitHub unreachable: {exc}") from exc

        if resp.status_code in (200, 201):
            return
        if resp.status_code in (409, 422):
            raise ConflictError(f"{path} changed underneath this write ({resp.status_code}).")
        if resp.status_code in (401, 403):
            raise StoreError("GitHub rejected the user-data token (expired, revoked, or missing Contents write access).")
        if resp.status_code == 404:
            raise StoreError(f"Branch '{self._branch}' does not exist in {self._repo}.")
        raise StoreError(f"GitHub write failed ({resp.status_code}): {self._message(resp)}")


class MemoryBackend:
    """In-memory stand-in with the same contract as GitHubBackend (including sha conflicts),
    for tests and local runs without a token."""

    def __init__(self):
        self.files: dict[str, tuple[dict, int]] = {}
        self.writes: list[tuple[str, str]] = []  # (path, message) per successful write
        self.fail_next_write: Exception | None = None
        self.fail_reads: Exception | None = None

    def read(self, path):
        if self.fail_reads:
            raise self.fail_reads
        if path not in self.files:
            return None, None
        doc, version = self.files[path]
        return json.loads(json.dumps(doc)), str(version)

    def write(self, path, doc, sha, message):
        if self.fail_next_write:
            exc, self.fail_next_write = self.fail_next_write, None
            raise exc
        current = self.files.get(path)
        if (current is None) != (sha is None) or (current is not None and str(current[1]) != sha):
            raise ConflictError(f"{path} sha mismatch")
        version = (current[1] + 1) if current else 1
        self.files[path] = (json.loads(json.dumps(doc)), version)
        self.writes.append((path, _commit_message(message)))


class UserStore:
    """Read / read-modify-write over a backend. `update` is the only way callers write."""

    def __init__(self, backend):
        self._backend = backend

    def read(self, path: str, default: Callable[[], dict]) -> dict:
        doc, _sha = self._backend.read(path)
        return default() if doc is None else doc

    def update(
        self,
        path: str,
        fn: Callable[[dict], dict],
        default: Callable[[], dict],
        message: str,
        retries: int = 1,
    ) -> dict:
        """doc = read (or default if absent); new = fn(doc); write if it actually changed.

        A stale sha (someone else wrote between our read and write) re-reads and re-applies
        `fn` up to `retries` more times, so `fn` must be a pure doc -> doc function (it is
        re-run on the fresh document, never replayed over a stale one). A no-op `fn` makes no
        commit at all. Returns the document as stored.
        """
        for attempt in range(retries + 1):
            doc, sha = self._backend.read(path)
            if doc is None:
                doc = default()
            new_doc = fn(json.loads(json.dumps(doc)))
            if new_doc == doc:
                return doc
            try:
                self._backend.write(path, new_doc, sha, message)
                return new_doc
            except ConflictError:
                if attempt == retries:
                    raise
        raise AssertionError("unreachable")  # pragma: no cover
