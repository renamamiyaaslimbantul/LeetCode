
# ArchiveSweep: collision-safe file deduplication

Estimated implementation time: 60 minutes.

## Scenario

ArchiveSweep is the deduplication stage of a production archive-ingestion
service. It recursively scans file and directory inputs, eliminates unnecessary
I/O early, and reports files whose contents are exactly equal even in the
presence of hash collisions and per-file filesystem failures.

The repository provides the stable public data model, constructor validation,
and stage-hook signatures. Implement the production deduplication pipeline in
`deduplicator.py`.

## What to build

Implement `deduplicator.py` using the provided scaffold. Preserve the public dataclasses, the
`FileDeduplicator` constructor, the `scan` signature, and the following two test hooks:

    _sample_fingerprint(path, size) -> (sha256_hex, bytes_read)
    _full_digest(path) -> (sha256_hex, bytes_read)

Tests observe these two stage boundaries. They do not require a particular
scheduler, data structure, or standard-library I/O API. Use only the Python
standard library.

## Required behavior

### Recursive discovery and path policy

- Each input is interpreted as a file or directory root path.
- Walk directories recursively without following symbolic links.
- Path identity is `str(Path(path))`.
- Process each exact path identity only once, including overlapping roots.
- Missing paths, symlinks, special files, and discovery errors become FileIssue
  values and do not stop unrelated files.
- FileIssue.error must include the underlying exception message. Its exact
  prefix or exception-type formatting is not graded.
- files_considered is the number of distinct regular files discovered, even if
  a later required stage fails.

### Size-first filtering

Group regular files by the size recorded during discovery. A file with a unique
size cannot be a duplicate, so it must not enter the sample or full-hash
stages. Avoid unnecessary readability checks. The tests do not prescribe or
instrument a particular standard-library open API. If a required sample, hash,
or comparison operation encounters an I/O error, report it as an issue.

### Non-overlapping sample filter

For each file in a repeated-size group:

- read up to sample_size bytes from the beginning;
- then read up to sample_size bytes then read up to sample_size bytes from the end of the file;
- never read the overlapping region twice;
- return the SHA-256 hex digest and number of bytes actually read.

Only files with the same size and sample digest proceed to full hashing.

### Streaming full hash

Compute SHA-256 incrementally with bounded memory. Do not retain an entire
candidate file in memory. You may choose any suitable standard-library I/O API
and syscall size.

### Exact, collision-safe grouping

A matching full digest does not prove that two files are equal. Verify them with
a bounded-memory, byte-for-byte comparison. You may use any standard-library
I/O API. A synthetic digest collision may involve multiple separate groups of files with equal contents.

Within each exact group, use the lexically smallest path as the canonical path
and sort the remaining duplicate paths. Sort groups by canonical path and issues
by `(path, error)`.

### Failure and accounting rules

- Metadata, sampling, hashing, and exact-comparison failures are isolated.
- Attribute an exact-comparison failure to the path whose open/read failed.
- Keep unrelated duplicate groups.
- Deduplicate repeated issues for the same path; retain the most recent required-stage issue.
- bytes_hashed counts bytes returned by successfully completed sample and
  full-hash hooks only. A hook that raises contributes zero, even if the
  underlying stream read some bytes before failing.
- Bytewise verification reads are excluded from bytes_hashed.

Empty input is valid. Constructor limits must be positive.

## Extended behavior

Run the sample and full-hash stages concurrently, with no more than
`max_workers` hashing hooks active at once. If at least `max_workers` eligible
hooks are blocked, the implementation must be able to run `max_workers` of them
at the same time. The tests do not grade executor queue depth or scheduler
structure.

## Bonus discussion

After implementing the scanner, answer the questions in `REFLECTION.essay`. They cover topics including files that change during a scan, hard links and inode identity, and resource bounds for different storage systems. These answers are discussion
material; hidden tests do not grade additional behavior from them.

## Example

Suppose the scan discovers these files:

- `a.txt` and `c.txt` contain the same bytes.
- `b.txt` and `d.txt` contain the same different bytes.
- `missing.txt` is an input path that does not exist.

The report contains:

    DuplicateGroup(
        canonical="a.txt",
        duplicates=("c.txt",),
        size=<file size>,
    )

    DuplicateGroup(
        canonical="b.txt",
        duplicates=("d.txt",),
        size=<file size>,
    )

and an issue for `missing.txt`.

Groups are sorted by canonical path, and issues are reported without preventing
unrelated duplicate groups from being returned.

## Running the visible tests

    python3 -m pytest -q -p no:cacheprovider test_client.py

Do not modify README.md and test_client.py. Add regression tests where appropriate.