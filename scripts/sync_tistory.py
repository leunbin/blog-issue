#!/usr/bin/env python3
import json
import os
import re
import urllib.request
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree

RSS_URL = os.environ.get("TISTORY_RSS_URL", "https://eunkongee.tistory.com/rss")
REPOSITORY = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GITHUB_TOKEN"]

DATA_PATH = Path("data/published-posts.json")
README_PATH = Path("README.md")
START_MARKER = "<!-- TISTORY_POSTS_START -->"
END_MARKER = "<!-- TISTORY_POSTS_END -->"
ID_PATTERN = re.compile(r"^\[(CS-\d{3,})\]", re.IGNORECASE)


def request_json(url, method="GET", payload=None):
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "blog-issue-tistory-sync",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8")
        return json.loads(body) if body else None


def fetch_rss_items():
    request = urllib.request.Request(
        RSS_URL,
        headers={"User-Agent": "Mozilla/5.0 (GitHub Actions Tistory RSS Sync)"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        root = ElementTree.fromstring(response.read())

    items = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        published = (item.findtext("pubDate") or "").strip()
        match = ID_PATTERN.match(title)
        if not match or not link:
            continue
        items.append(
            {
                "id": match.group(1).upper(),
                "title": title,
                "url": link,
                "published": published,
            }
        )
    return items


def fetch_study_issues():
    issues = {}
    for page in range(1, 11):
        url = (
            f"https://api.github.com/repos/{REPOSITORY}/issues"
            f"?state=all&per_page=100&page={page}"
        )
        batch = request_json(url)
        if not batch:
            break

        for issue in batch:
            if "pull_request" in issue:
                continue
            match = ID_PATTERN.match(issue.get("title", ""))
            if match:
                issues[match.group(1).upper()] = issue

        if len(batch) < 100:
            break
    return issues


def extract_category(body):
    body = body or ""
    patterns = [
        r"\*\*분야:\*\*\s*([^\n]+)",
        r"##\s*분야\s*\n+\s*([^\n]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, body, re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            if value and not value.startswith("<!--"):
                return value
    return "-"


def parse_date(value):
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return ""


def escape_cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def update_readme(posts):
    readme = README_PATH.read_text(encoding="utf-8")
    rows = [
        "| ID | 분야 | 질문 | 티스토리 | 완료일 |",
        "|---|---|---|---|---|",
    ]
    for post in sorted(posts, key=lambda item: int(item["id"].split("-")[1])):
        rows.append(
            "| {id} | {category} | {question} | [글 보기]({url}) | {date} |".format(
                id=escape_cell(post["id"]),
                category=escape_cell(post["category"]),
                question=escape_cell(post["question"]),
                url=post["url"],
                date=post["published_at"],
            )
        )

    replacement = START_MARKER + "\n" + "\n".join(rows) + "\n" + END_MARKER
    pattern = re.compile(
        re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER),
        re.DOTALL,
    )
    if not pattern.search(readme):
        raise RuntimeError("README markers were not found")
    README_PATH.write_text(pattern.sub(replacement, readme), encoding="utf-8")


def main():
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    posts = (
        json.loads(DATA_PATH.read_text(encoding="utf-8"))
        if DATA_PATH.exists()
        else []
    )
    known_ids = {post["id"] for post in posts}
    issues = fetch_study_issues()

    for rss_item in fetch_rss_items():
        study_id = rss_item["id"]
        issue = issues.get(study_id)
        if not issue or study_id in known_ids:
            continue

        issue_number = issue["number"]
        if issue["state"] == "open":
            comment = (
                "티스토리 정리 글을 확인했어요. ✅\n\n"
                f"- {rss_item['title']}\n"
                f"- {rss_item['url']}\n\n"
                "학습을 완료한 것으로 기록하고 이슈를 닫습니다."
            )
            request_json(
                f"https://api.github.com/repos/{REPOSITORY}/issues/{issue_number}/comments",
                method="POST",
                payload={"body": comment},
            )
            request_json(
                f"https://api.github.com/repos/{REPOSITORY}/issues/{issue_number}",
                method="PATCH",
                payload={"state": "closed", "state_reason": "completed"},
            )

        question = ID_PATTERN.sub("", issue["title"], count=1).strip()
        posts.append(
            {
                "id": study_id,
                "category": extract_category(issue.get("body")),
                "question": question,
                "url": rss_item["url"],
                "published_at": parse_date(rss_item["published"]),
                "issue_number": issue_number,
            }
        )
        known_ids.add(study_id)

    DATA_PATH.write_text(
        json.dumps(posts, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    update_readme(posts)


if __name__ == "__main__":
    main()
