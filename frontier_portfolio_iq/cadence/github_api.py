"""Minimal GitHub Issues client (REST, standard library only) and an in-memory fake for tests/dry runs."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol


class IssueTracker(Protocol):
    def ensure_label(self, name: str, color: str = "1F3A5F", description: str = "") -> None: ...
    def ensure_milestone(self, title: str, due_on: Optional[str] = None) -> Optional[int]: ...
    def issues(self, label: str) -> List[Dict]: ...
    def create_issue(self, title: str, body: str, labels: List[str], assignees: List[str],
                     milestone: Optional[int] = None) -> Dict: ...
    def update_issue(self, number: int, **fields) -> Dict: ...
    def comments(self, number: int) -> List[Dict]: ...
    def comment(self, number: int, body: str) -> Dict: ...


class GitHubIssues:
    def __init__(self, repo: Optional[str] = None, token: Optional[str] = None,
                 api: str = "https://api.github.com"):
        self.repo = repo or os.environ["GITHUB_REPOSITORY"]
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not self.token:
            raise RuntimeError("GITHUB_TOKEN is not set")
        self.api = api.rstrip("/")

    def _call(self, method: str, path: str, body=None):
        url = path if path.startswith("http") else f"{self.api}/repos/{self.repo}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "frontier-portfolio-iq"})
        if data is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}

    def _paged(self, path: str) -> List[Dict]:
        out, page = [], 1
        while True:
            sep = "&" if "?" in path else "?"
            batch = self._call("GET", f"{path}{sep}per_page=100&page={page}")
            out.extend(batch)
            if len(batch) < 100:
                return out
            page += 1

    def ensure_label(self, name, color="1F3A5F", description=""):
        try:
            self._call("POST", "/labels", {"name": name, "color": color, "description": description[:100]})
        except urllib.error.HTTPError as e:
            if e.code != 422:  # 422 = already exists
                raise

    def ensure_milestone(self, title, due_on=None):
        for m in self._paged("/milestones?state=all"):
            if m["title"] == title:
                return m["number"]
        body = {"title": title}
        if due_on:
            body["due_on"] = f"{due_on}T23:59:59Z"
        return self._call("POST", "/milestones", body)["number"]

    def issues(self, label):
        return [i for i in self._paged(f"/issues?state=all&labels={urllib.request.quote(label)}")
                if "pull_request" not in i]

    def create_issue(self, title, body, labels, assignees, milestone=None):
        payload = {"title": title, "body": body, "labels": labels, "assignees": assignees}
        if milestone:
            payload["milestone"] = milestone
        try:
            return self._call("POST", "/issues", payload)
        except urllib.error.HTTPError as e:
            if e.code == 422 and assignees:   # an owner without repo access cannot be assigned
                payload["assignees"] = []
                return self._call("POST", "/issues", payload)
            raise

    def update_issue(self, number, **fields):
        return self._call("PATCH", f"/issues/{number}", fields)

    def comments(self, number):
        return self._paged(f"/issues/{number}/comments")

    def comment(self, number, body):
        return self._call("POST", f"/issues/{number}/comments", {"body": body})


@dataclass
class FakeIssues:
    """In-memory tracker used by tests and `--dry-run`."""
    labels: Dict[str, str] = field(default_factory=dict)
    milestones: Dict[str, int] = field(default_factory=dict)
    store: List[Dict] = field(default_factory=list)
    notes: Dict[int, List[Dict]] = field(default_factory=dict)

    def ensure_label(self, name, color="1F3A5F", description=""):
        self.labels.setdefault(name, color)

    def ensure_milestone(self, title, due_on=None):
        return self.milestones.setdefault(title, len(self.milestones) + 1)

    def issues(self, label):
        return [i for i in self.store if label in i["labels"]]

    def create_issue(self, title, body, labels, assignees, milestone=None):
        issue = {"number": len(self.store) + 1, "title": title, "body": body, "labels": list(labels),
                 "assignees": list(assignees), "state": "open", "milestone": milestone}
        self.store.append(issue)
        self.notes[issue["number"]] = []
        return issue

    def update_issue(self, number, **fields):
        issue = next(i for i in self.store if i["number"] == number)
        issue.update(fields)
        return issue

    def comments(self, number):
        return self.notes.get(number, [])

    def comment(self, number, body):
        c = {"body": body}
        self.notes.setdefault(number, []).append(c)
        return c
