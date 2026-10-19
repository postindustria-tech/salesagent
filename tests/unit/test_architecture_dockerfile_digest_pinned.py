"""Guard: Dockerfile supply-chain pins (digest ARGs, FROM pins, non-root USER).

Per D34 + PR 5 of issue #1234. Four independent checks:

- ``assert_dockerfile_digest_args_present`` — digest ARG **presence + sha256 shape
  only**; does not verify the digest matches pinned ``PYTHON_VERSION`` / ``UV_VERSION``.
- ``find_unpinned_dockerfile_from_lines`` — every external ``FROM`` must reference
  ``@sha256:…`` or a digest ``ARG`` substitution, not a tag-only image ref.
- ``runtime_user_directives`` — runtime stage must not end as root.
- ``useradd_lines_without_home`` — the runtime user must get a home directory.
"""

from __future__ import annotations

import pytest

from tests.unit._architecture_helpers import (
    _ROOT_USER_DIRECTIVES,
    assert_dockerfile_digest_args_present,
    find_unpinned_dockerfile_from_lines,
    format_failure,
    repo_root,
    runtime_user_directives,
)


def useradd_lines_without_home(lines: list[str]) -> list[str]:
    """``useradd`` commands that create no home directory.

    ``useradd -r`` creates none by default, but ``$HOME`` still points at
    ``/home/<user>``. Libraries that cache under ``~`` (googleads/zeep) then
    fail with ``[Errno 13] Permission denied: '/home/app'`` and GAM sync breaks.
    """
    return [line.strip() for line in lines if "useradd " in line and not ({"-m", "--create-home"} & set(line.split()))]


_KNOWN_BAD_FROM = [
    "FROM ghcr.io/astral-sh/uv:0.11.15 AS uv",
    "FROM node:20-slim AS build",
]


@pytest.mark.arch_guard
def test_dockerfile_digest_args_present() -> None:
    """Digest ARG pins present with sha256:<64-hex> shape (not version-digest match)."""
    assert_dockerfile_digest_args_present((repo_root() / "Dockerfile").read_text(encoding="utf-8"))


@pytest.mark.arch_guard
def test_dockerfile_digest_args_detector_catches_missing_pins() -> None:
    """Mutation self-test: Dockerfile without digest ARGs must fail the guard."""
    with pytest.raises(AssertionError, match="Dockerfile missing digest ARG pins"):
        assert_dockerfile_digest_args_present("ARG PYTHON_VERSION=3.12\nFROM python:3.12-slim\n")


@pytest.mark.arch_guard
def test_dockerfile_base_image_digest_pinned() -> None:
    """Every external FROM must include @sha256: or digest ARG — not tag-only."""
    lines = (repo_root() / "Dockerfile").read_text(encoding="utf-8").splitlines()
    violations = find_unpinned_dockerfile_from_lines(lines)
    assert not violations, format_failure(
        summary="Dockerfile has tag-only external FROM lines",
        violations=violations,
    )


@pytest.mark.arch_guard
def test_dockerfile_digest_detector_catches_known_bad_from() -> None:
    assert find_unpinned_dockerfile_from_lines(_KNOWN_BAD_FROM), "Detector must flag tag-only external FROM lines"


@pytest.mark.arch_guard
def test_dockerfile_runs_as_non_root() -> None:
    """Runtime stage must end with USER set to a non-root identity."""
    lines = (repo_root() / "Dockerfile").read_text(encoding="utf-8").splitlines()
    user_lines = runtime_user_directives(lines)
    assert user_lines, "Dockerfile runtime stage has no USER directive (D34)"
    last_user = user_lines[-1]
    assert last_user not in _ROOT_USER_DIRECTIVES, format_failure(
        summary="Dockerfile runtime stage runs as root",
        violations=[last_user],
    )


@pytest.mark.arch_guard
def test_dockerfile_non_root_detector_catches_root_group_form() -> None:
    assert "USER root:root" in _ROOT_USER_DIRECTIVES
    assert "USER 0:0" in _ROOT_USER_DIRECTIVES


@pytest.mark.arch_guard
def test_dockerfile_runtime_user_has_home() -> None:
    """The runtime user is created with a home directory it owns."""
    lines = (repo_root() / "Dockerfile").read_text(encoding="utf-8").splitlines()
    violations = useradd_lines_without_home(lines)
    assert not violations, format_failure(
        summary="Dockerfile creates a user without a home directory (useradd needs -m)",
        violations=violations,
    )


@pytest.mark.arch_guard
def test_dockerfile_home_detector_catches_useradd_without_m() -> None:
    assert useradd_lines_without_home(["RUN useradd -r -u 1001 -g app -s /usr/sbin/nologin app"])
    assert not useradd_lines_without_home(["RUN useradd -r -m -d /home/app -u 1001 -g app app"])
