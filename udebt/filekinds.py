"""What kind of file a path is, from its name alone: lockfile, generated, media, dependency
manifest, infrastructure, test, docs or code. Shared by commit weights, history mining,
line-share attribution and structural clustering."""

from __future__ import annotations

import re

MANIFESTS = re.compile(
    r"(^|/)(pyproject\.toml|setup\.py|setup\.cfg|requirements[^/]*\.txt|Pipfile|environment\.ya?ml|"
    r"package\.json|go\.mod|Cargo\.toml|Gemfile|pom\.xml|build\.gradle(\.kts)?|composer\.json|"
    r"mix\.exs|deno\.json|pubspec\.yaml)$"
)
INFRA = re.compile(
    r"(^|/)(Dockerfile[^/]*|docker-compose[^/]*\.ya?ml|Makefile|Procfile|fly\.toml|vercel\.json|"
    r"netlify\.toml|render\.yaml|app\.yaml|serverless\.ya?ml|[^/]*\.plist|nginx[^/]*\.conf|"
    r"run\.sh|deploy[^/]*\.sh)$|^\.github/workflows/|^\.gitlab-ci\.yml$|^terraform/|^infra/|"
    r"^deploy/|^k8s/|^helm/"
)
LOCK = re.compile(
    r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|uv\.lock|Pipfile\.lock|"
    r"Cargo\.lock|go\.sum|composer\.lock|Gemfile\.lock|flake\.lock|[^/]*\.lock)$"
)
GENERATED = re.compile(
    r"(^|/)(dist|build|out|target|node_modules|__pycache__|\.next|coverage|__snapshots__)/|"
    r"\.(min\.js|min\.css|map|pyc|snap|ipynb_checkpoints)$|(^|/)[^/]*\.generated\.[^/]+$"
)
DOCS = re.compile(r"(^|/)(docs?|doc)/|\.(md|rst|txt|adoc)$|(^|/)(LICENSE|CHANGELOG|README)[^/]*$")
TESTS = re.compile(r"(^|/)(tests?|__tests__|spec|e2e)/|(^|/)test_[^/]+$|[._](test|spec)\.[a-z]+$")
MEDIA = re.compile(r"\.(png|jpe?g|gif|svg|ico|webp|mp3|mp4|wav|pdf|woff2?|ttf)$")


def kind(path: str) -> str:
    if LOCK.search(path):
        return "lock"
    if GENERATED.search(path):
        return "generated"
    if MEDIA.search(path):
        return "media"
    if MANIFESTS.search(path):
        return "manifest"
    if INFRA.search(path):
        return "infra"
    if TESTS.search(path):
        return "test"
    if DOCS.search(path):
        return "docs"
    return "code"


MECHANICAL = ("lock", "generated", "media")  # lines here say nothing about understanding


def mechanical(path: str) -> bool:
    return kind(path) in MECHANICAL
