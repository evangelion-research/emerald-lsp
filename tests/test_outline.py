import unittest

from lsprotocol import types

from emerald_lsp.outline import build

SOURCE = """\
import strings
from result import Result, Err as Failure

type Word = str
error NotFound { key: str }
dim Batch, Seq

const LIMIT = 10

def split(s: str, sep: str) -> list[str] pure {
    out: list[str] = []
    i = 0
    def _inner(x: int) -> int {
        local = x + 1
        return local
    }
    return out
}

def _private(x: int) -> int { return x }
"""


class TestOutline(unittest.TestCase):
    def setUp(self):
        self.outline = build(SOURCE)
        self.by_name = {d.name: d for d in self.outline.symbols}

    def test_top_level_symbols(self):
        self.assertEqual(
            [d.name for d in self.outline.symbols],
            ["strings", "Result", "Failure", "Word", "NotFound", "Batch", "Seq",
             "LIMIT", "split", "_private"],
        )

    def test_kinds(self):
        self.assertEqual(self.by_name["Word"].kind, types.SymbolKind.Interface)
        self.assertEqual(self.by_name["NotFound"].kind, types.SymbolKind.Struct)
        self.assertEqual(self.by_name["Batch"].kind, types.SymbolKind.TypeParameter)
        self.assertEqual(self.by_name["LIMIT"].kind, types.SymbolKind.Constant)
        self.assertEqual(self.by_name["split"].kind, types.SymbolKind.Function)
        self.assertEqual(self.by_name["strings"].kind, types.SymbolKind.Module)

    def test_detail_is_the_signature(self):
        self.assertEqual(
            self.by_name["split"].detail, "def split(s: str, sep: str) -> list[str] pure"
        )

    def test_underscore_is_private(self):
        # module.c:235 is_private -- and an import binding is never re-exported
        self.assertTrue(self.by_name["split"].exported)
        self.assertFalse(self.by_name["_private"].exported)
        self.assertFalse(self.by_name["strings"].exported)
        self.assertEqual(
            [d.name for d in self.outline.exports()],
            ["Word", "NotFound", "Batch", "Seq", "LIMIT", "split"],
        )

    def test_nested_defs_are_children(self):
        children = [c.name for c in self.by_name["split"].children]
        self.assertIn("_inner", children)
        self.assertIn("out", children)
        self.assertNotIn("local", children)  # it belongs to _inner

    def test_locals_do_not_leak_out_of_their_function(self):
        offset = SOURCE.index("def _private")
        self.assertIsNone(self.outline.resolve("out", offset))
        self.assertIsNotNone(self.outline.resolve("LIMIT", offset))

    def test_parameters_are_in_scope_in_the_body(self):
        offset = SOURCE.index("i = 0")
        self.assertIsNotNone(self.outline.resolve("sep", offset))
        self.assertEqual(self.outline.resolve("sep", offset).detail, "sep: str")

    def test_a_binding_is_not_visible_before_its_declaration(self):
        self.assertIsNone(self.outline.resolve("LIMIT", SOURCE.index("type Word")))

    def test_functions_are_visible_before_their_declaration(self):
        # top-level names link as a set, so forward references are legal
        self.assertIsNotNone(self.outline.resolve("split", 0))

    def test_imports(self):
        plain, from_import = self.outline.imports
        self.assertEqual((plain.kind, plain.module_path), ("import", "strings"))
        self.assertEqual(plain.local_module_name, "strings")
        self.assertEqual(from_import.module_path, "result")
        self.assertEqual(
            [(n.name, n.alias, n.local) for n in from_import.names],
            [("Result", None, "Result"), ("Err", "Failure", "Failure")],
        )

    def test_aliased_import_binds_the_alias(self):
        outline = build("import text.strings as s\n")
        self.assertEqual(outline.imports[0].local_module_name, "s")
        self.assertEqual([d.name for d in outline.symbols], ["s"])

    def test_dotted_import_binds_the_last_component(self):
        self.assertEqual(build("import a.b.c\n").imports[0].local_module_name, "c")

    def test_folding_covers_multiline_blocks(self):
        self.assertTrue(any(r.end.line > r.start.line for r in self.outline.folds))

    def test_broken_input_still_yields_symbols(self):
        outline = build("def good() { }\ndef ")
        self.assertEqual([d.name for d in outline.symbols], ["good"])

    def test_a_brace_on_the_binding_line_is_not_part_of_it(self):
        outline = build("def f() {\n  if c { x = 1 }\n}\n")
        binding = outline.definitions[-1]
        self.assertEqual(binding.detail, "x = 1")


if __name__ == "__main__":
    unittest.main()
