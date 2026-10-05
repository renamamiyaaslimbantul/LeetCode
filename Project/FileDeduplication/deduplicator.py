"""Starter scaffold for the ArchiveSweep production deduplication pipeline."""

from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

_CHUNK = 1 << 16


@dataclass(frozen=True)
class DuplicateGroup:
    canonical: str
    duplicates: tuple[str, ...]
    size: int


@dataclass(frozen=True)
class FileIssue:
    path: str
    error: str


@dataclass(frozen=True)
class ScanReport:
    groups: tuple[DuplicateGroup, ...]
    issues: tuple[FileIssue, ...]
    files_considered: int
    bytes_hashed: int


@dataclass
class _Candidate:
    path: str
    size: int
    sample_digest: str | None = None
    full_digest: str | None = None


class FileDeduplicator:
    def __init__(self, max_workers: int = 4, sample_size: int = 4096):
        if max_workers < 1 or sample_size < 1:
            raise ValueError("limits must be positive")
        self.max_workers = max_workers
        self.sample_size = sample_size

    # -- test hooks ---------------------------------------------------------

    def _full_digest(self, path: Path) -> tuple[str, int]:
        """Return the file's full-content fingerprint and bytes read."""
        digest = hashlib.sha256()
        total = 0
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(_CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
                total += len(chunk)
        return digest.hexdigest(), total

    def _sample_fingerprint(self, path: Path, size: int) -> tuple[str, int]:
        """Return a sample-based fingerprint and bytes read for a file."""
        head_n = min(self.sample_size, size)
        tail_n = min(self.sample_size, max(size - head_n, 0))
        digest = hashlib.sha256()
        read = 0
        with open(path, "rb") as handle:
            head = handle.read(head_n)
            digest.update(head)
            read += len(head)
            if tail_n:
                handle.seek(size - tail_n)
                tail = handle.read(tail_n)
                digest.update(tail)
                read += len(tail)
        return digest.hexdigest(), read

    # -- pipeline -----------------------------------------------------------

    def scan(self, paths: Iterable[str | Path]) -> ScanReport:
        """Scan the supplied roots and return all duplicate groups and issues."""
        issues: dict[str, str] = {}
        candidates: dict[str, int] = {}
        seen_paths: set[str] = set()

        for root in paths:
            root_path = Path(root)
            self._discover(root_path, seen_paths, candidates, issues)

        files_considered = len(candidates)

        # -- size-first filtering ------------------------------------------
        by_size: dict[int, list[str]] = defaultdict(list)
        for path, size in candidates.items():
            by_size[size].append(path)
        repeated = {
            size: sorted(group)
            for size, group in by_size.items()
            if len(group) > 1
        }
        sample_targets = [path for group in repeated.values() for path in group]

        # -- sample stage (concurrent, bounded) ----------------------------
        def run_sample(path: str) -> tuple[str, tuple[str, int] | None, str | None]:
            try:
                digest, read = self._sample_fingerprint(
                    Path(path), candidates[path]
                )
                return path, (digest, read), None
            except OSError as exc:
                return path, None, str(exc)
            except Exception as exc:  # pragma: no cover - defensive
                return path, None, "{}: {}".format(type(exc).__name__, exc)

        sample_results = self._run_parallel(run_sample, sample_targets)

        bytes_hashed = 0
        by_sample: dict[tuple[int, str], list[str]] = defaultdict(list)
        for path, result, error in sample_results:
            if error is not None:
                issues[path] = error
                continue
            assert result is not None
            digest, read = result
            bytes_hashed += read
            by_sample[(candidates[path], digest)].append(path)

        sample_candidates = {
            key: sorted(group) for key, group in by_sample.items() if len(group) > 1
        }
        hash_targets = [path for group in sample_candidates.values() for path in group]

        # -- full-hash stage (concurrent, bounded) -------------------------
        def run_full(path: str) -> tuple[str, tuple[str, int] | None, str | None]:
            try:
                digest, read = self._full_digest(Path(path))
                return path, (digest, read), None
            except OSError as exc:
                return path, None, str(exc)
            except Exception as exc:  # pragma: no cover - defensive
                return path, None, "{}: {}".format(type(exc).__name__, exc)

        full_results = self._run_parallel(run_full, hash_targets)

        by_full: dict[tuple[int, str], list[str]] = defaultdict(list)
        for path, result, error in full_results:
            if error is not None:
                issues[path] = error
                continue
            assert result is not None
            digest, read = result
            bytes_hashed += read
            by_full[(candidates[path], digest)].append(path)

        full_candidates = {
            key: sorted(group) for key, group in by_full.items() if len(group) > 1
        }

        # -- exact, collision-safe grouping --------------------------------
        groups: list[DuplicateGroup] = []
        for (size, _digest), group in full_candidates.items():
            exact = self._exact_groups(group, issues)
            for members in exact:
                if len(members) < 2:
                    continue
                members = sorted(members)
                groups.append(
                    DuplicateGroup(
                        canonical=members[0],
                        duplicates=tuple(members[1:]),
                        size=size,
                    )
                )

        groups.sort(key=lambda g: g.canonical)
        issue_values = tuple(
            FileIssue(path=path, error=error)
            for path, error in sorted(issues.items())
        )
        return ScanReport(
            groups=tuple(groups),
            issues=issue_values,
            files_considered=files_considered,
            bytes_hashed=bytes_hashed,
        )

    # -- helpers ------------------------------------------------------------

    def _run_parallel(self, func, targets):
        if not targets:
            return []
        if self.max_workers == 1 or len(targets) == 1:
            return [func(target) for target in targets]
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            return list(pool.map(func, targets))

    def _discover(
        self,
        root: Path,
        seen_paths: set[str],
        candidates: dict[str, int],
        issues: dict[str, str],
    ) -> None:
        identity = str(root)
        if identity in seen_paths:
            return
        seen_paths.add(identity)

        # Classify the root without following symlinks. A failure to read
        # metadata becomes a single issue for this path and never stops
        # unrelated files from being returned.
        try:
            is_link = os.path.islink(identity)
            is_dir = os.path.isdir(identity)
            is_file = os.path.isfile(identity)
            st = os.stat(identity, follow_symlinks=False)
        except OSError as exc:
            issues[identity] = str(exc)
            return
        except ValueError as exc:  # e.g. embedded NUL on some platforms
            issues[identity] = str(exc)
            return

        if is_link:
            issues[identity] = "symbolic link is not a regular file"
            return

        if is_dir:
            stack = [identity]
            while stack:
                current = stack.pop()
                try:
                    entries = list(os.scandir(current))
                except OSError as exc:
                    issues[current] = str(exc)
                    continue
                for entry in entries:
                    child = entry.path
                    if child in seen_paths:
                        continue
                    seen_paths.add(child)
                    self._classify_entry(entry, child, stack, candidates, issues)
            return

        if not is_file:
            issues[identity] = "not a regular file"
            return
        candidates[identity] = st.st_size

    def _classify_entry(
        self,
        entry,
        child: str,
        stack: list[str],
        candidates: dict[str, int],
        issues: dict[str, str],
    ) -> None:
        """Classify one directory entry, isolating any metadata failure.

        Any error while reading this entry's metadata is recorded as a single
        issue for ``child``; the caller keeps scanning the remaining entries.
        """
        try:
            if entry.is_symlink():
                issues[child] = "symbolic link is not a regular file"
                return
            if entry.is_dir(follow_symlinks=False):
                stack.append(child)
                return
            if entry.is_file(follow_symlinks=False):
                candidates[child] = entry.stat(follow_symlinks=False).st_size
                return
            issues[child] = "not a regular file"
        except OSError as exc:
            issues[child] = str(exc)
        except ValueError as exc:  # malformed name/path
            issues[child] = str(exc)

    def _exact_groups(
        self, group: list[str], issues: dict[str, str]
    ) -> list[list[str]]:
        remaining = list(group)
        result: list[list[str]] = []
        while remaining:
            anchor = remaining[0]
            rest = remaining[1:]
            bucket = [anchor]
            still: list[str] = []
            anchor_failed = False
            for other in rest:
                equal, err, failed_path = self._compare(anchor, other)
                if err is not None:
                    issues[failed_path] = err
                    if failed_path == anchor:
                        # The anchor is unusable; stop using it and keep the
                        # remaining candidates for further grouping.
                        anchor_failed = True
                        still.append(other)
                    continue
                if equal:
                    bucket.append(other)
                else:
                    still.append(other)
            if anchor_failed:
                result.append([])
                remaining = still
                continue
            result.append(bucket)
            remaining = still
        return [members for members in result if members]

    def _compare(self, left: str, right: str) -> tuple[bool, str | None, str]:
        """Byte-for-byte comparison of two files with bounded memory.

        Returns (equal, error, failed_path). On failure the error is
        attributed to the file whose open/read failed.
        """
        try:
            fh_left = open(left, "rb")
        except OSError as exc:
            return False, str(exc), left
        with fh_left:
            try:
                fh_right = open(right, "rb")
            except OSError as exc:
                return False, str(exc), right
            with fh_right:
                while True:
                    try:
                        a = fh_left.read(_CHUNK)
                    except OSError as exc:
                        return False, str(exc), left
                    try:
                        b = fh_right.read(_CHUNK)
                    except OSError as exc:
                        return False, str(exc), right
                    if a != b:
                        return False, None, ""
                    if not a:
                        return True, None, ""
