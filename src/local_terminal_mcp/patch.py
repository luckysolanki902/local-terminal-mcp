"""Multi-file V4A patch application, gated by the policy.

The per-file diff matcher lives in the vendored :mod:`apply_diff` module
(OpenAI Agents SDK, MIT). This module owns the ``*** Begin Patch`` envelope and
*all* filesystem access, so every path a patch names still passes through
:meth:`Policy.resolve_path` and write-gating, exactly like the other tools.

Two phases, so a patch is atomic: :func:`parse_patch` validates structure with
no I/O, then :func:`apply_patch` resolves paths, computes every new file in
memory (any mismatch aborts with zero writes), and only then writes — rolling
back already-written files from in-memory snapshots if a later write fails.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path

from .apply_diff import apply_diff
from .policy import Policy, PolicyError

BEGIN_PATCH = "*** Begin Patch"
END_PATCH = "*** End Patch"
UPDATE_FILE = "*** Update File: "
ADD_FILE = "*** Add File: "
DELETE_FILE = "*** Delete File: "
MOVE_TO = "*** Move to: "

_HEADERS = (UPDATE_FILE, ADD_FILE, DELETE_FILE)

# A patch envelope this large is almost certainly a mistake (e.g. a whole file
# pasted as context); refuse rather than churn. A single resulting file over the
# per-file cap is likewise refused — large binaries go through write_file_base64.
MAX_PATCH_CHARS = 200_000
MAX_FILE_BYTES = 1_000_000


class PatchSyntaxError(ValueError):
    """The patch envelope is malformed. ``line`` is 1-based when known."""

    def __init__(self, message: str, line: int | None = None) -> None:
        self.line = line
        super().__init__(message)


@dataclass
class FileOp:
    kind: str  # "update" | "add" | "delete"
    path: str
    move_to: str | None
    body: list[str]
    header_line: int  # 1-based line of this op's header, for error messages


def _is_header(line: str) -> bool:
    return line.startswith(_HEADERS)


def parse_patch(text: str) -> list[FileOp]:
    """Validate the envelope and return the file operations. Pure; no I/O.

    Checks only *structure*: the envelope is present, headers are known, paths
    are present and unique, deletes carry no body, updates carry a hunk, and no
    move target collides. Whether a hunk actually matches a file is decided
    later, in :func:`apply_patch`, against the real file contents.
    """
    if len(text) > MAX_PATCH_CHARS:
        raise PatchSyntaxError(
            f"patch is {len(text)} chars; exceeds the {MAX_PATCH_CHARS}-char limit"
        )
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    n = len(lines)

    i = 0
    while i < n and lines[i].strip() == "":
        i += 1
    if i >= n or lines[i].strip() != BEGIN_PATCH:
        raise PatchSyntaxError(f"patch must start with '{BEGIN_PATCH}'", line=i + 1)
    i += 1

    ops: list[FileOp] = []
    seen: set[str] = set()
    move_targets: set[str] = set()
    ended = False

    while i < n:
        line = lines[i]
        stripped = line.strip()
        if stripped == "":
            i += 1
            continue
        if stripped == END_PATCH:
            ended = True
            i += 1
            break
        if not _is_header(line):
            raise PatchSyntaxError(
                f"expected a file header, got: {line!r}", line=i + 1
            )

        header_line = i + 1
        if line.startswith(UPDATE_FILE):
            kind, path = "update", line[len(UPDATE_FILE) :].strip()
        elif line.startswith(ADD_FILE):
            kind, path = "add", line[len(ADD_FILE) :].strip()
        else:
            kind, path = "delete", line[len(DELETE_FILE) :].strip()
        i += 1
        if not path:
            raise PatchSyntaxError(f"{kind} header has no path", line=header_line)

        move_to: str | None = None
        if kind == "update" and i < n and lines[i].startswith(MOVE_TO):
            move_to = lines[i][len(MOVE_TO) :].strip()
            if not move_to:
                raise PatchSyntaxError("'*** Move to:' has no path", line=i + 1)
            i += 1

        body: list[str] = []
        while i < n and not _is_header(lines[i]) and lines[i].strip() != END_PATCH:
            body.append(lines[i])
            i += 1

        if kind == "delete":
            if any(b.strip() for b in body):
                raise PatchSyntaxError(
                    "'*** Delete File:' takes no body", line=header_line
                )
        elif not any(b.strip() for b in body):
            raise PatchSyntaxError(
                f"'{'*** Add File:' if kind == 'add' else '*** Update File:'}' "
                "has no content",
                line=header_line,
            )

        if path in seen:
            raise PatchSyntaxError(f"duplicate path in patch: {path}", line=header_line)
        seen.add(path)
        if move_to is not None:
            if move_to in move_targets:
                raise PatchSyntaxError(
                    f"two files move to the same path: {move_to}", line=header_line
                )
            move_targets.add(move_to)

        ops.append(FileOp(kind, path, move_to, body, header_line))

    if not ended:
        raise PatchSyntaxError(f"patch is missing '{END_PATCH}'")
    if not ops:
        raise PatchSyntaxError("patch contains no file operations")
    return ops


def _read_text(target: Path, rel: str) -> str:
    """Read a UTF-8 text file exactly, refusing binaries and non-UTF-8 content."""
    raw = target.read_bytes()
    if b"\x00" in raw:
        raise PolicyError(
            f"{rel!r} looks binary; patch text files only "
            "(use write_file_base64 for binary assets)"
        )
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise PolicyError(f"{rel!r} is not valid UTF-8 text") from None


def _rel(policy: Policy, target: Path) -> str:
    try:
        return str(target.relative_to(policy.root))
    except ValueError:
        return str(target)


@dataclass
class _Write:
    kind: str  # "A" | "M" | "D" | "R"
    rel_from: str
    rel_to: str | None
    old: str | None  # prior content for diffing/rollback (None for pure add)
    new: str | None  # new content (None for delete)
    target: Path  # where new content lands (the move-to path for a rename)
    remove: Path | None  # source path to delete (rename/delete), else None


def apply_patch(policy: Policy, text: str) -> str:
    """Apply a parsed patch atomically and return a human-readable summary."""
    policy.require_write()
    ops = parse_patch(text)

    planned: list[_Write] = []
    for op in ops:
        src = policy.resolve_path(op.path)
        rel_src = _rel(policy, src)

        if op.kind == "add":
            if src.exists():
                raise PolicyError(f"{rel_src!r} already exists; use Update File")
            try:
                new = apply_diff("", "\n".join(op.body), mode="create")
            except ValueError as exc:
                raise PolicyError(
                    f"could not build added file {rel_src!r}: {exc}"
                ) from None
            _guard_size(new, rel_src)
            planned.append(
                _Write("A", rel_src, None, None, new, src, None)
            )

        elif op.kind == "delete":
            if not src.is_file():
                raise PolicyError(f"{rel_src!r} is not a file")
            old = _read_text(src, rel_src)
            planned.append(_Write("D", rel_src, None, old, None, src, src))

        else:  # update (optionally a rename)
            if not src.is_file():
                raise PolicyError(f"{rel_src!r} is not a file")
            old = _read_text(src, rel_src)
            try:
                new = apply_diff(old, "\n".join(op.body), mode="default")
            except ValueError as exc:
                raise PolicyError(
                    f"patch did not apply to {rel_src!r}: {exc}; "
                    "re-read the file and rebuild the hunk"
                ) from None
            _guard_size(new, rel_src)
            if op.move_to is not None:
                dest = policy.resolve_path(op.move_to)
                rel_dest = _rel(policy, dest)
                if dest.exists():
                    raise PolicyError(f"move target {rel_dest!r} already exists")
                planned.append(
                    _Write("R", rel_src, rel_dest, old, new, dest, src)
                )
            else:
                planned.append(_Write("M", rel_src, None, old, new, src, None))

    _commit(planned)
    return _render(policy, planned)


def _guard_size(content: str, rel: str) -> None:
    size = len(content.encode("utf-8"))
    if size > MAX_FILE_BYTES:
        raise PolicyError(
            f"{rel!r} would be {size} bytes; exceeds the "
            f"{MAX_FILE_BYTES}-byte per-file cap"
        )


def _commit(planned: list[_Write]) -> None:
    """Perform the writes, rolling back on any mid-batch failure.

    Add/Modify write new content to the target. Rename writes new content to the
    destination and removes the source. Delete removes the source.
    """
    done: list[_Write] = []
    try:
        for w in planned:
            if w.new is not None:
                w.target.parent.mkdir(parents=True, exist_ok=True)
                w.target.write_text(w.new, encoding="utf-8")
            if w.kind in ("D", "R") and w.remove is not None:
                w.remove.unlink()
            done.append(w)
    except OSError as exc:
        _rollback(done)
        raise PolicyError(
            f"write failed after {len(done)} of {len(planned)} file(s); "
            f"rolled back: {exc}"
        ) from None


def _rollback(done: list[_Write]) -> None:
    """Best-effort restore of files already written in this batch."""
    for w in reversed(done):
        try:
            if w.kind == "A":
                w.target.unlink(missing_ok=True)
            elif w.kind == "M":
                if w.old is not None:
                    w.target.write_text(w.old, encoding="utf-8")
            elif w.kind == "D":
                if w.old is not None and w.remove is not None:
                    w.remove.write_text(w.old, encoding="utf-8")
            elif w.kind == "R":
                w.target.unlink(missing_ok=True)
                if w.old is not None and w.remove is not None:
                    w.remove.write_text(w.old, encoding="utf-8")
        except OSError:
            pass


def _render(policy: Policy, planned: list[_Write]) -> str:
    counts = {"A": 0, "M": 0, "D": 0, "R": 0}
    summary: list[str] = []
    for w in planned:
        counts[w.kind] += 1
        if w.kind == "R":
            summary.append(f"R {w.rel_from} -> {w.rel_to}")
        else:
            summary.append(f"{w.kind} {w.rel_from}")

    totals = ", ".join(
        f"{n} {label}"
        for n, label in (
            (counts["A"], "added"),
            (counts["M"], "modified"),
            (counts["R"], "renamed"),
            (counts["D"], "deleted"),
        )
        if n
    )
    head = [f"applied patch: {totals}", *summary]
    header_text = "\n".join(head)

    diffs: list[str] = []
    for w in planned:
        if w.kind == "D":
            continue
        old = (w.old or "").splitlines(keepends=True)
        new = (w.new or "").splitlines(keepends=True)
        label_to = w.rel_to or w.rel_from
        diff = difflib.unified_diff(
            old, new, fromfile=f"a/{w.rel_from}", tofile=f"b/{label_to}"
        )
        diffs.append("".join(diff))
    diff_text = "\n".join(d for d in diffs if d)

    if not diff_text:
        return header_text
    budget = policy.max_output_bytes
    combined = header_text + "\n\n" + diff_text
    if len(combined.encode("utf-8", "replace")) <= budget:
        return combined
    # Summary is never dropped; the diff is clipped to the remaining budget.
    room = max(0, budget - len(header_text.encode("utf-8")) - 64)
    clipped = diff_text.encode("utf-8", "replace")[:room].decode("utf-8", "ignore")
    return header_text + "\n\n" + clipped + "\n[... diff truncated ...]"


__all__ = ["parse_patch", "apply_patch", "PatchSyntaxError"]
