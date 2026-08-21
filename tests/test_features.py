import tempfile
import unittest
from pathlib import Path

from lsprotocol import types

from emerald_lsp import features
from emerald_lsp.outline import build

MAIN = """\
import strings
from result import Result

const LIMIT = 3

def greet(name: str) -> str {
    prefix = "hi "
    return prefix + strings.upper(name)
}
"""

STRINGS = """\
def upper(s: str) -> str pure { return s }
def _hidden(s: str) -> str { return s }
type Word = str
"""

RESULT = "type Result[T, E] = { ok: bool }\n"


def position(line, character):
    return types.Position(line=line, character=character)


class FeatureCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "strings.rald").write_text(STRINGS)
        (self.dir / "result.rald").write_text(RESULT)
        self.main = self.dir / "main.rald"
        self.main.write_text(MAIN)
        self.ctx = self.context(MAIN)

    def context(self, source):
        return features.Context(
            path=str(self.main),
            source=source,
            outline=build(source),
            include_paths=[],
            compiler=None,
        )


class TestHover(FeatureCase):
    def test_hover_on_a_local_shows_its_declaration(self):
        line = MAIN.splitlines().index("    prefix = \"hi \"")
        hover = features.hover(self.ctx, position(line, 5))
        self.assertIn('prefix = "hi "', hover.contents.value)

    def test_hover_on_a_module_binding_shows_the_file_it_resolved_to(self):
        hover = features.hover(self.ctx, position(0, 8))
        self.assertIn(str(self.dir / "strings.rald"), hover.contents.value)

    def test_hover_on_a_qualified_name_reads_the_other_module(self):
        line = MAIN.splitlines().index("    return prefix + strings.upper(name)")
        hover = features.hover(self.ctx, position(line, MAIN.splitlines()[line].index("upper")))
        self.assertIn("def upper(s: str) -> str pure", hover.contents.value)

    def test_hover_on_a_builtin(self):
        ctx = self.context("len(xs)\n")
        self.assertIn("builtin", features.hover(ctx, position(0, 1)).contents.value)

    def test_hover_on_a_keyword(self):
        ctx = self.context("const x = 1\n")
        self.assertIn("keyword", features.hover(ctx, position(0, 2)).contents.value)

    def test_hover_on_punctuation_is_nothing(self):
        self.assertIsNone(features.hover(self.ctx, position(3, 12)))


class TestDefinition(FeatureCase):
    def test_local_definition(self):
        line = MAIN.splitlines().index("    return prefix + strings.upper(name)")
        found = features.definition(self.ctx, position(line, 12))
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].uri.endswith("main.rald"))

    def test_definition_across_a_module_boundary(self):
        line = MAIN.splitlines().index("    return prefix + strings.upper(name)")
        column = MAIN.splitlines()[line].index("upper")
        found = features.definition(self.ctx, position(line, column))
        self.assertTrue(found[0].uri.endswith("strings.rald"))
        self.assertEqual(found[0].range.start.line, 0)

    def test_definition_on_an_import_path_opens_the_module(self):
        found = features.definition(self.ctx, position(0, 8))
        self.assertTrue(found[0].uri.endswith("strings.rald"))

    def test_definition_on_a_lifted_name(self):
        found = features.definition(self.ctx, position(1, 20))
        self.assertTrue(found[0].uri.endswith("result.rald"))

    def test_an_unknown_name_has_no_definition(self):
        ctx = self.context("nowhere\n")
        self.assertEqual(features.definition(ctx, position(0, 2)), [])


class TestCompletion(FeatureCase):
    def labels(self, ctx, pos):
        return [item.label for item in features.completions(ctx, pos).items]

    def test_scope_completion_offers_locals_keywords_and_builtins(self):
        line = MAIN.splitlines().index("    prefix = \"hi \"")
        labels = self.labels(self.ctx, position(line, 4))
        for expected in ("name", "LIMIT", "greet", "const", "len", "seq"):
            self.assertIn(expected, labels)

    def test_completion_after_a_module_binding_lists_its_exports(self):
        source = MAIN.replace("strings.upper(name)", "strings.")
        ctx = self.context(source)
        line = source.splitlines().index("    return prefix + strings.")
        labels = self.labels(ctx, position(line, len("    return prefix + strings.")))
        self.assertIn("upper", labels)
        self.assertIn("Word", labels)
        self.assertNotIn("_hidden", labels)  # module.c is_private

    def test_completion_after_a_value_stays_silent(self):
        # a field list needs the checker's type for the receiver (DESIGN.md 4b)
        source = "def f(p) { p. }\n"
        ctx = self.context(source)
        result = features.completions(ctx, position(0, 13))
        self.assertEqual(result.items, [])
        self.assertTrue(result.is_incomplete)

    def test_import_completion_lists_module_paths(self):
        ctx = self.context("import ")
        self.assertIn("strings", self.labels(ctx, position(0, 7)))

    def test_from_import_completion_lists_the_modules_exports(self):
        ctx = self.context("from strings import ")
        labels = self.labels(ctx, position(0, 20))
        self.assertEqual(sorted(labels), ["Word", "upper"])


class TestSymbolsAndReferences(FeatureCase):
    def test_document_symbols_nest_and_deduplicate(self):
        source = "def f() {\n  i = 0\n  i = i + 1\n}\n"
        symbols = features.document_symbols(build(source))
        self.assertEqual([s.name for s in symbols], ["f"])
        self.assertEqual([c.name for c in symbols[0].children], ["i"])

    def test_references_are_name_matches_in_this_file(self):
        line = MAIN.splitlines().index("    prefix = \"hi \"")
        found = features.references(self.ctx, position(line, 5))
        self.assertEqual(len(found), 2)

    def test_folding_ranges_end_before_the_closing_brace(self):
        source = "def f() {\n  x = 1\n}\n"
        fold = features.folding_ranges(build(source))[0]
        self.assertEqual((fold.start_line, fold.end_line), (0, 1))


if __name__ == "__main__":
    unittest.main()
