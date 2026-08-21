import unittest

from emerald_lsp.lexer import token_at, tokenize


def kinds(src):
    return [(t.kind, t.value) for t in tokenize(src)]


class TestLexer(unittest.TestCase):
    def test_keywords_and_idents(self):
        self.assertEqual(
            kinds("def f(x)"),
            [("keyword", "def"), ("ident", "f"), ("op", "("), ("ident", "x"), ("op", ")")],
        )

    def test_comment_is_kept(self):
        self.assertEqual(kinds("x # note\ny"),
                         [("ident", "x"), ("comment", "# note"), ("ident", "y")])

    def test_strings_and_fstrings(self):
        self.assertEqual(kinds('"a" f"b{x}" \'c\''),
                         [("str", '"a"'), ("fstr", 'f"b{x}"'), ("str", "'c'")])

    def test_escaped_quote_does_not_end_the_string(self):
        self.assertEqual(kinds(r'"a\"b"'), [("str", r'"a\"b"')])

    def test_unterminated_string_recovers(self):
        # the C lexer stops the stream here; an editor buffer needs the rest
        toks = tokenize('x = "oops\ny = 1')
        self.assertEqual(toks[2].kind, "error")
        self.assertIn(("ident", "y"), [(t.kind, t.value) for t in toks])

    def test_unknown_character_recovers(self):
        self.assertEqual(kinds("a $ b"),
                         [("ident", "a"), ("error", "$"), ("ident", "b")])

    def test_numbers(self):
        self.assertEqual(kinds("1 2.5 3. 1e-3"),
                         [("int", "1"), ("float", "2.5"), ("float", "3."), ("float", "1e-3")])

    def test_field_access_on_int_is_not_a_float(self):
        self.assertEqual(kinds("1.foo"), [("int", "1"), ("op", "."), ("ident", "foo")])

    def test_multi_character_operators(self):
        self.assertEqual([t.value for t in tokenize("a |> b >> c => d -> e == f")][1::2],
                         ["|>", ">>", "=>", "->", "=="])

    def test_positions_count_characters_not_bytes(self):
        # "é" is two bytes, one character: the token after it starts at 4
        toks = tokenize('x = "é" + y')
        plus = [t for t in toks if t.value == "+"][0]
        self.assertEqual((plus.line, plus.col), (0, 8))

    def test_multiline_string_spans_lines(self):
        token = tokenize('"a\nb"')[0]
        self.assertEqual((token.line, token.end_line, token.end_col), (0, 1, 2))

    def test_token_at_includes_the_right_edge(self):
        toks = tokenize("hello world")
        self.assertEqual(token_at(toks, 0, 5).value, "hello")  # cursor after "hello"
        self.assertEqual(token_at(toks, 0, 7).value, "world")


if __name__ == "__main__":
    unittest.main()
