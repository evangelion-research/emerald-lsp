"""Compiler diagnostics -> LSP diagnostics.

The mapping itself is mechanical (DESIGN.md 5, last bullet). The interesting
parts are the two conversions it has to do on the way, and one gap it has to
paper over:

* 1-based byte columns become 0-based character columns (`positions`).
* `end_line`/`end_col` do not exist yet (DESIGN.md 1b/4d), so a range is
  synthesized by widening the start position over the token that begins there.
  Every range in this module gets narrower and more accurate the day the AST
  carries end positions -- and nothing else has to change.
"""

from __future__ import annotations

from lsprotocol import types

from .lexer import tokenize
from .positions import byte_col_to_char_col, line_text, path_to_uri, range_of

SOURCE = "emeraldc"

_SEVERITY = {
    "error": types.DiagnosticSeverity.Error,
    "warning": types.DiagnosticSeverity.Warning,
    "note": types.DiagnosticSeverity.Information,
    "hint": types.DiagnosticSeverity.Hint,
}


def to_lsp(diag: dict, source_lines: dict[str, list[str]]) -> types.Diagnostic | None:
    """Convert one diagnostic. `source_lines` maps a file path to its lines,
    so ranges can be widened using the text the editor actually has."""
    file = diag.get("file")
    line = diag.get("line")
    if not isinstance(file, str) or not isinstance(line, int):
        return None

    lines = source_lines.get(file)
    if lines is None:
        # multi-module runs report files that are not open; the compiler
        # already quoted the offending line for us
        quoted = diag.get("source_line")
        lines = [quoted] if isinstance(quoted, str) else []
        lineno = 0 if lines else max(line - 1, 0)
    else:
        lineno = max(line - 1, 0)

    text = line_text(lines, lineno if lines else 0)
    column = diag.get("column") if isinstance(diag.get("column"), int) else 1
    start = byte_col_to_char_col(text, column)
    end = _token_end(text, start)

    message = diag.get("message") or "error"
    expected, actual = diag.get("expected"), diag.get("actual")
    if isinstance(expected, str) and isinstance(actual, str):
        message = f"{message}\n  expected: {expected}\n  actual:   {actual}"
    for note in diag.get("notes") or []:
        if isinstance(note, dict):
            message += f"\n  {note.get('label', 'note')}: {note.get('value', '')}"

    return types.Diagnostic(
        range=range_of(max(line - 1, 0), start, max(line - 1, 0), end),
        message=message,
        severity=_SEVERITY.get(diag.get("severity", "error"), types.DiagnosticSeverity.Error),
        code=diag.get("code"),
        source=SOURCE,
        data={"kind": diag.get("kind")} if diag.get("kind") else None,
    )


def _token_end(text: str, start: int) -> int:
    """Widen a point to the token starting there -- the caret in the human
    rendering points at a construct's first token (`docs/diagnostics.md`)."""
    if start >= len(text):
        return max(start + 1, len(text))
    for token in tokenize(text[start:]):
        if token.offset == 0 and token.end_line == 0:
            return start + token.end_col
        break
    return start + 1


def group_by_uri(
    diagnostics: list[dict], source_lines: dict[str, list[str]]
) -> dict[str, list[types.Diagnostic]]:
    """Bucket diagnostics per file: a multi-module check reports errors in
    files other than the one being edited, and those belong on those files."""
    out: dict[str, list[types.Diagnostic]] = {}
    for raw in diagnostics:
        converted = to_lsp(raw, source_lines)
        if converted is None:
            continue
        file = raw["file"]
        uri = path_to_uri(file) if file != "<stdlib>" else None
        if uri is None:
            continue
        out.setdefault(uri, []).append(converted)
    return out
