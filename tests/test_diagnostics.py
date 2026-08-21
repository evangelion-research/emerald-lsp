from lsprotocol import types

from emlsp.diagnostics import group_by_uri, to_lsp

DIAG = {
    "kind": "type",
    "severity": "error",
    "code": "E_TYPE_ARG",
    "file": "/tmp/foo.rald",
    "line": 3,
    "column": 2,
    "message": 'argument 1 of f(): expected int, got "x"',
    "expected": "int",
    "actual": '"x"',
    "source_line": 'f("x", "y")',
}


def test_line_and_column_become_zero_based():
    diag = to_lsp(DIAG, {"/tmp/foo.rald": ["", "", 'f("x", "y")']})
    assert diag.range.start == types.Position(line=2, character=1)


def test_range_widens_over_the_token_at_the_caret():
    source = {"/tmp/foo.rald": ["", "", "  value = 1"]}
    diag = to_lsp({**DIAG, "column": 3}, source)
    assert diag.range.end.character == len("  value")


def test_expected_and_actual_are_in_the_message():
    diag = to_lsp(DIAG, {})
    assert "expected: int" in diag.message
    assert 'actual:   "x"' in diag.message


def test_notes_are_appended():
    diag = to_lsp({**DIAG, "notes": [{"label": "defined", "value": "bar.rald:1"}]}, {})
    assert "defined: bar.rald:1" in diag.message


def test_byte_columns_are_converted():
    line = 'print("héllo", oops)'
    byte_col = line.encode().index(b"oops") + 1
    diag = to_lsp({**DIAG, "line": 1, "column": byte_col}, {"/tmp/foo.rald": [line]})
    assert diag.range.start.character == line.index("oops")


def test_severity_maps():
    assert (
        to_lsp({**DIAG, "severity": "warning"}, {}).severity
        == types.DiagnosticSeverity.Warning
    )


def test_unknown_file_falls_back_to_the_quoted_source_line():
    diag = to_lsp(DIAG, {})
    assert diag is not None
    assert diag.range.start.line == 2


def test_grouping_splits_by_file():
    other = {**DIAG, "file": "/tmp/bar.rald"}
    grouped = group_by_uri([DIAG, other], {})
    assert len(grouped) == 2
    assert all(len(v) == 1 for v in grouped.values())


def test_stdlib_diagnostics_have_no_uri_and_are_dropped():
    assert group_by_uri([{**DIAG, "file": "<stdlib>"}], {}) == {}


def test_malformed_input_is_ignored():
    assert to_lsp({"message": "no location"}, {}) is None
