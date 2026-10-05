from deduplicator import FileDeduplicator


def test_finds_a_basic_duplicate_group(tmp_path):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    a.write_bytes(b"same")
    b.write_bytes(b"same")
    c.write_bytes(b"different")
    report = FileDeduplicator().scan([c, b, a])
    assert len(report.groups) == 1
    assert report.groups[0].canonical == str(a)
    assert report.groups[0].duplicates == (str(b),)


def test_unique_sizes_are_not_duplicates(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"a")
    b.write_bytes(b"bb")
    assert FileDeduplicator().scan([a, b]).groups == ()


def test_invalid_configuration():
    for workers, sample in [(0, 1), (1, 0)]:
        try:
            FileDeduplicator(workers, sample)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid configuration was accepted")
