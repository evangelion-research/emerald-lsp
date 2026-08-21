import unittest

from emerald_lsp.positions import byte_col_to_char_col, path_to_uri, uri_to_path


class TestPositions(unittest.TestCase):
    def test_ascii_is_the_identity_modulo_the_base(self):
        self.assertEqual(byte_col_to_char_col("abc", 1), 0)
        self.assertEqual(byte_col_to_char_col("abc", 3), 2)

    def test_non_ascii_shifts_every_later_column(self):
        line = 'print("héllo", x)'
        # "x" is at byte column 17 but character column 15
        byte_col = line.encode().index(b"x") + 1
        self.assertEqual(byte_col_to_char_col(line, byte_col), line.index("x"))

    def test_emoji_outside_the_bmp(self):
        line = 'x = "🙂" + y'
        byte_col = line.encode().index(b"+") + 1
        self.assertEqual(byte_col_to_char_col(line, byte_col), line.index("+"))

    def test_every_character_boundary_maps_back(self):
        line = "é🙂 abc"
        for char_col in range(len(line)):
            byte_col = len(line[:char_col].encode("utf-8")) + 1
            self.assertEqual(byte_col_to_char_col(line, byte_col), char_col)

    def test_past_the_end_clamps(self):
        self.assertEqual(byte_col_to_char_col("ab", 99), 2)

    def test_uri_round_trip(self):
        uri = path_to_uri("/tmp/a b/f.rald")
        self.assertTrue(uri.startswith("file://"))
        self.assertTrue(uri_to_path(uri).endswith("/a b/f.rald"))

    def test_non_file_scheme_is_not_a_path(self):
        self.assertIsNone(uri_to_path("untitled:Untitled-1"))


if __name__ == "__main__":
    unittest.main()
