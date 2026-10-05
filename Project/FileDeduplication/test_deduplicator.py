import hashlib
import os

import pytest

from deduplicator import DuplicateGroup, FileDeduplicator, ScanReport


def paths(report):
    return [(g.canonical, g.duplicates, g.size) for g in report.groups]


def test_basic_group(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"same")
    b.write_bytes(b"same")
    r = FileDeduplicator().scan([b, a])
    assert paths(r) == [(str(a), (str(b),), 4)]


def test_multiple_groups_and_missing_input(tmp_path):
    a, c = tmp_path / "a.txt", tmp_path / "c.txt"
    b, d = tmp_path / "b.txt", tmp_path / "d.txt"
    a.write_bytes(b"x")
    c.write_bytes(b"x")
    b.write_bytes(b"yyy")
    d.write_bytes(b"yyy")
    missing = tmp_path / "missing.txt"
    r = FileDeduplicator().scan([c, b, a, missing, d])
    assert paths(r) == [(str(a), (str(c),), 1), (str(b), (str(d),), 3)]
    assert len(r.issues) == 1
    assert r.issues[0].path == str(missing)
    assert r.files_considered == 4


def test_unique_size_never_sampled(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"a")
    b.write_bytes(b"bb")
    calls = []
    orig = FileDeduplicator._sample_fingerprint

    def spy(self, path, size):
        calls.append(str(path))
        return orig(self, path, size)

    monkeypatch.setattr(FileDeduplicator, "_sample_fingerprint", spy)
    r = FileDeduplicator().scan([a, b])
    assert r.groups == ()
    assert calls == []


def test_recursive_discovery_and_overlapping_roots(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    a = tmp_path / "a"
    b = sub / "b"
    a.write_bytes(b"dup")
    b.write_bytes(b"dup")
    r = FileDeduplicator().scan([tmp_path, sub, a])
    assert r.files_considered == 2
    assert paths(r) == [(str(a), (str(b),), 3)]


def test_sample_reads_head_and_tail_bounded(tmp_path):
    p = tmp_path / "big"
    p.write_bytes(b"a" * 100 + b"middle" * 10 + b"z" * 100)
    d = FileDeduplicator(sample_size=8)
    digest, read = d._sample_fingerprint(p, 100 + 60 + 100)
    expected = hashlib.sha256(b"a" * 8 + b"z" * 8).hexdigest()
    assert digest == expected
    assert read == 16


def test_sample_small_file_no_overlap(tmp_path):
    p = tmp_path / "small"
    p.write_bytes(b"hello")
    d = FileDeduplicator(sample_size=4096)
    digest, read = d._sample_fingerprint(p, 5)
    assert read == 5
    assert digest == hashlib.sha256(b"hello").hexdigest()


def test_sample_exact_boundary_no_double_read(tmp_path):
    # size == 2 * sample_size => head and tail tile the whole file once.
    p = tmp_path / "b"
    p.write_bytes(b"abcdefgh")
    d = FileDeduplicator(sample_size=4)
    digest, read = d._sample_fingerprint(p, 8)
    assert read == 8
    assert digest == hashlib.sha256(b"abcdefgh").hexdigest()


def test_same_size_different_sample_not_hashed(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"aaaa")
    b.write_bytes(b"bbbb")
    full_calls = []
    orig = FileDeduplicator._full_digest

    def spy(self, path):
        full_calls.append(str(path))
        return orig(self, path)

    monkeypatch.setattr(FileDeduplicator, "_full_digest", spy)
    r = FileDeduplicator().scan([a, b])
    assert r.groups == ()
    assert full_calls == []


def test_bytes_hashed_counts_successful_hooks(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"same")
    b.write_bytes(b"same")
    r = FileDeduplicator(sample_size=2).scan([a, b])
    # sample: head 2 + tail 2 = 4 bytes each; full: 4 bytes each.
    assert r.bytes_hashed == 4 * 2 + 4 * 2


def test_synthetic_collision_verified_bytewise(tmp_path, monkeypatch):
    # Two same-size, same-sample, same-full-digest files whose contents differ.
    same_size = 4096
    real_a, real_b = tmp_path / "real_a", tmp_path / "real_b"
    real_a.write_bytes(b"a" * same_size)
    real_b.write_bytes(b"b" * same_size)
    # A distinct truly-duplicate pair that must survive.
    dup1, dup2 = tmp_path / "dup1", tmp_path / "dup2"
    dup1.write_bytes(b"c" * same_size)
    dup2.write_bytes(b"c" * same_size)

    def colliding_full(self, path):
        return "0" * 64, same_size

    monkeypatch.setattr(FileDeduplicator, "_full_digest", colliding_full)

    def colliding_sample(self, path, size):
        return "0" * 64, 0

    monkeypatch.setattr(FileDeduplicator, "_sample_fingerprint", colliding_sample)

    r = FileDeduplicator().scan([real_a, real_b, dup1, dup2])
    assert paths(r) == [(str(dup1), (str(dup2),), same_size)]


def test_collision_multiple_separate_groups(tmp_path, monkeypatch):
    same = 4096
    files = {}
    for name, ch in [
        ("a1", b"a"),
        ("a2", b"a"),
        ("b1", b"b"),
        ("b2", b"b"),
    ]:
        p = tmp_path / name
        p.write_bytes(ch * same)
        files[name] = p
    monkeypatch.setattr(FileDeduplicator, "_full_digest", lambda self, p: ("0" * 64, same))
    monkeypatch.setattr(FileDeduplicator, "_sample_fingerprint", lambda self, p, s: ("0" * 64, 0))
    r = FileDeduplicator().scan(list(files.values()))
    assert paths(r) == [
        (str(files["a1"]), (str(files["a2"]),), same),
        (str(files["b1"]), (str(files["b2"]),), same),
    ]


def test_hook_failure_isolated_and_attributed(tmp_path, monkeypatch):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    for p in (a, b, c):
        p.write_bytes(b"same")
    real = FileDeduplicator._full_digest

    def flaky(self, path):
        if str(path) == str(b):
            raise OSError("boom-read")
        return real(self, path)

    monkeypatch.setattr(FileDeduplicator, "_full_digest", flaky)
    r = FileDeduplicator().scan([a, b, c])
    assert paths(r) == [(str(a), (str(c),), 4)]
    assert [i.path for i in r.issues] == [str(b)]
    assert "boom-read" in r.issues[0].error


def test_failing_hook_contributes_no_bytes(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"x" * 100)
    b.write_bytes(b"x" * 100)

    real = FileDeduplicator._sample_fingerprint

    def partial(self, path, size):
        if str(path) == str(a):
            raise OSError("failed after some reads")
        return real(self, path, size)

    monkeypatch.setattr(FileDeduplicator, "_sample_fingerprint", partial)
    r = FileDeduplicator().scan([a, b])
    # a (the failing hook) contributed 0 bytes even though it may have read
    # some bytes internally; b's sample succeeded and read 100 bytes. No full
    # stage runs because no sample group has >= 2 members.
    assert r.bytes_hashed == 100
    assert r.groups == ()


def test_missing_and_special_paths_become_issues(tmp_path):
    missing = tmp_path / "nope"
    r = FileDeduplicator().scan([missing])
    assert r.groups == ()
    assert len(r.issues) == 1
    assert r.files_considered == 0


def test_directory_is_not_an_issue(tmp_path):
    (tmp_path / "a").write_bytes(b"x")
    r = FileDeduplicator().scan([tmp_path])
    assert r.issues == ()
    assert r.files_considered == 1


def test_empty_input():
    r = FileDeduplicator().scan([])
    assert r == ScanReport(groups=(), issues=(), files_considered=0, bytes_hashed=0)


def test_lexical_canonical_and_sorted_duplicates(tmp_path):
    names = ["z", "a", "m"]
    for n in names:
        (tmp_path / n).write_bytes(b"dup")
    r = FileDeduplicator().scan([tmp_path / n for n in names])
    assert r.groups[0].canonical == str(tmp_path / "a")
    assert r.groups[0].duplicates == (str(tmp_path / "m"), str(tmp_path / "z"))


def test_constructor_validation():
    for workers, sample in [(0, 1), (1, 0), (-1, 1), (1, -1)]:
        with pytest.raises(ValueError):
            FileDeduplicator(workers, sample)


def test_concurrent_hooks_run_in_parallel(tmp_path):
    import threading
    import time

    n = 4
    for i in range(n):
        (tmp_path / str(i)).write_bytes(b"same")
    dt = FileDeduplicator(max_workers=n)
    lock = threading.Lock()
    active = 0
    peak = 0
    orig = FileDeduplicator._full_digest

    def blocking(self, path):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.1)
        with lock:
            active -= 1
        return orig(self, path)

    blocking_attr = FileDeduplicator._full_digest
    FileDeduplicator._full_digest = blocking
    try:
        dt.scan([tmp_path / str(i) for i in range(n)])
    finally:
        FileDeduplicator._full_digest = blocking_attr
    assert peak >= 2


def test_exact_compare_failure_attributed_to_other(tmp_path, monkeypatch):
    # anchor a is fine; b fails to open during bytewise comparison.
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    for p in (a, b, c):
        p.write_bytes(b"same")

    real_open = open

    def fake_open(path, *args, **kwargs):
        if str(path) == str(b) and "rb" in args:
            raise OSError("open-denied")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)
    r = FileDeduplicator().scan([a, b, c])
    # a and c still group; b is an issue.
    assert paths(r) == [(str(a), (str(c),), 4)]
    assert [i.path for i in r.issues] == [str(b)]
    assert "open-denied" in r.issues[0].error


def test_metadata_read_failure_isolated_single_issue(tmp_path, monkeypatch):
    # A directory contains three regular files; reading metadata for the
    # middle one fails. The failure must become exactly one issue and must
    # not lose the other two valid files (which still dedupe).
    good1 = tmp_path / "good1"
    bad = tmp_path / "bad"
    good2 = tmp_path / "good2"
    good1.write_bytes(b"same")
    bad.write_bytes(b"same")
    good2.write_bytes(b"same")

    real_scandir = os.scandir

    class _BadStatEntry:
        def __init__(self, entry):
            self._entry = entry
            self.path = entry.path

        def is_symlink(self):
            return self._entry.is_symlink()

        def is_dir(self, follow_symlinks=False):
            return self._entry.is_dir(follow_symlinks=follow_symlinks)

        def is_file(self, follow_symlinks=False):
            return self._entry.is_file(follow_symlinks=follow_symlinks)

        def stat(self, follow_symlinks=False):
            raise OSError("metadata boom")

    def fake_scandir(path):
        for entry in real_scandir(path):
            if entry.path == str(bad):
                yield _BadStatEntry(entry)
            else:
                yield entry

    monkeypatch.setattr(os, "scandir", fake_scandir)
    r = FileDeduplicator().scan([tmp_path])

    assert r.files_considered == 2
    assert paths(r) == [(str(good1), (str(good2),), 4)]
    assert [i.path for i in r.issues] == [str(bad)]
    assert "metadata boom" in r.issues[0].error


def test_root_metadata_failure_single_issue_keeps_others(tmp_path, monkeypatch):
    # An unreadable root becomes one issue; a sibling root still scans.
    broken = tmp_path / "broken"
    ok = tmp_path / "ok"
    broken.write_bytes(b"x")
    ok.write_bytes(b"y")

    real_stat = os.stat

    def fake_stat(path, *args, **kwargs):
        if str(path) == str(broken):
            raise OSError("stat boom")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", fake_stat)
    r = FileDeduplicator().scan([broken, ok])

    assert r.files_considered == 1
    assert [i.path for i in r.issues] == [str(broken)]
    assert "stat boom" in r.issues[0].error


def test_duplicate_issue_keeps_most_recent(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"x" * 10)
    b.write_bytes(b"x" * 10)

    def bad_sample(self, path, size):
        raise OSError("sample-fail")

    monkeypatch.setattr(FileDeduplicator, "_sample_fingerprint", bad_sample)
    r = FileDeduplicator().scan([a, b])
    assert len(r.issues) == 2
    assert {i.path for i in r.issues} == {str(a), str(b)}
