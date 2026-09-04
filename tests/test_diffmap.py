from __future__ import annotations

import hashlib

from creasy.review.findings import Finding
from creasy.review.position import build_position_variants, line_code
from creasy.workspace.diffmap import parse_unified_diff

ADDED = """diff --git a/src/buf.cpp b/src/buf.cpp
new file mode 100644
--- /dev/null
+++ b/src/buf.cpp
@@ -0,0 +1,3 @@
+char dest[8];
+strcpy(dest, src);
+return dest;
"""

CHANGED = """diff --git a/src/leak.cpp b/src/leak.cpp
--- a/src/leak.cpp
+++ b/src/leak.cpp
@@ -1,3 +1,4 @@
 int* make_buffer() {
-    return 0;
+    return new int[32];
 }
+int unused = 1;
"""

DELETED = """diff --git a/old.cpp b/old.cpp
deleted file mode 100644
--- a/old.cpp
+++ /dev/null
@@ -1,2 +0,0 @@
-void gone() {}
-int x = 1;
"""


def test_parse_added_file_maps_new_lines() -> None:
    diff = parse_unified_diff(ADDED)
    file_diff = diff.find("src/buf.cpp")
    assert file_diff is not None
    assert file_diff.added
    old, new, kind = file_diff.resolve_new(2)
    assert kind == "added"
    assert old is None
    assert new == 2


def test_parse_changed_file_maps_added_and_context() -> None:
    diff = parse_unified_diff(CHANGED)
    file_diff = diff.find("src/leak.cpp")
    assert file_diff is not None
    old, new, kind = file_diff.resolve_new(2)
    assert kind == "added"
    assert old is None
    assert new == 2
    old, new, kind = file_diff.resolve_new(1)
    assert kind == "context"
    assert old == 1
    assert new == 1


def test_parse_deleted_file_maps_old_lines() -> None:
    diff = parse_unified_diff(DELETED)
    file_diff = diff.find("old.cpp")
    assert file_diff is not None
    old, new, kind = file_diff.resolve_old(1)
    assert kind == "deleted"
    assert old == 1
    assert new is None


def test_position_for_added_range() -> None:
    finding = Finding(
        path="src/buf.cpp",
        start_line=1,
        end_line=3,
        side="new",
        severity="critical",
        title="overflow",
        body="strcpy",
    )
    variants = build_position_variants(
        finding,
        parse_unified_diff(ADDED),
        base_sha="aaa",
        start_sha="aaa",
        head_sha="bbb",
    )
    assert variants
    pos = variants[0]
    assert pos["new_path"] == "src/buf.cpp"
    assert pos["new_line"] == 1
    assert "old_line" not in pos
    assert pos["head_sha"] == "bbb"
    assert "line_range" in pos
    start = pos["line_range"]["start"]
    assert start["line_code"] == line_code("src/buf.cpp", None, 1)
    assert start["type"] == "new"


def test_position_skipped_when_file_not_in_diff() -> None:
    finding = Finding(
        path="missing.cpp",
        start_line=1,
        end_line=1,
        side="new",
        severity="minor",
        title="x",
        body="y",
    )
    assert build_position_variants(
        finding,
        parse_unified_diff(ADDED),
        base_sha="a",
        start_sha="a",
        head_sha="b",
    ) == []


def test_line_code_is_sha1_path() -> None:
    digest = hashlib.sha1(b"x.cpp").hexdigest()
    assert line_code("x.cpp", None, 30) == f"{digest}_0_30"
