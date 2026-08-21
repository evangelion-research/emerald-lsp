"""End-to-end: the real server process, driven over stdio.

These are the "session tests" of DESIGN.md 8. They are the only tests that
exercise framing, the lifecycle, the handler signatures, the debounce, and the
UTF-16 conversion together -- everything that can be perfectly right in
isolation and still leave a client seeing nothing.
"""

import os
import tempfile
import unittest
from pathlib import Path

from tests.session import Session
from tests.test_compiler import write_fake

MAIN = """\
import strings

def greet(name: str) -> str {
    return strings.upper(name)
}
"""

STRINGS = "def upper(s: str) -> str pure { return s }\n"


class TestSession(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        (cls.dir / "strings.rald").write_text(STRINGS)
        cls.main = cls.dir / "main.rald"
        cls.main.write_text(MAIN)
        cls.binary = write_fake(cls.dir)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def session(self, with_compiler: bool = False) -> Session:
        if with_compiler:
            os.environ["EMERALDC"] = self.binary
            self.addCleanup(os.environ.pop, "EMERALDC", None)
        else:
            os.environ.pop("EMERALDC", None)
        return Session(self.dir)

    def test_syntax_features_work_without_a_compiler(self):
        with self.session() as client:
            uri = client.open(self.main)

            symbols = client.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}})
            self.assertEqual([s["name"] for s in symbols], ["strings", "greet"])

            tokens = client.request("textDocument/semanticTokens/full", {"textDocument": {"uri": uri}})
            self.assertEqual(len(tokens["data"]) % 5, 0)
            self.assertGreater(len(tokens["data"]), 0)

            hover = client.request(
                "textDocument/hover",
                {"textDocument": {"uri": uri}, "position": {"line": 0, "character": 8}},
            )
            self.assertIn("strings.rald", hover["contents"]["value"])

            definition = client.request(
                "textDocument/definition",
                {"textDocument": {"uri": uri}, "position": {"line": 3, "character": 21}},
            )
            self.assertTrue(definition[0]["uri"].endswith("strings.rald"))

    def test_completion_and_workspace_symbols(self):
        with self.session() as client:
            uri = client.open(self.main)
            completion = client.request(
                "textDocument/completion",
                {"textDocument": {"uri": uri}, "position": {"line": 3, "character": 4}},
            )
            self.assertIn("greet", [i["label"] for i in completion["items"]])

            found = client.request("workspace/symbol", {"query": "upper"})
            self.assertEqual([s["name"] for s in found], ["upper"])

    def test_diagnostics_are_published_for_an_open_document(self):
        with self.session(with_compiler=True) as client:
            client.open(self.main)
            params = client.wait_for("textDocument/publishDiagnostics")
            self.assertEqual(len(params["diagnostics"]), 1)
            self.assertEqual(params["diagnostics"][0]["code"], "E_TYPE_ARG")
            self.assertEqual(params["diagnostics"][0]["source"], "emeraldc")

    def test_editing_re_checks_the_unsaved_buffer(self):
        with self.session(with_compiler=True) as client:
            uri = client.open(self.main)
            client.wait_for("textDocument/publishDiagnostics")
            client.notify(
                "textDocument/didChange",
                {
                    "textDocument": {"uri": uri, "version": 2},
                    "contentChanges": [{"text": "clean\n"}],
                },
            )
            params = client.wait_for("textDocument/publishDiagnostics")
            self.assertEqual(params["diagnostics"], [])
            # the temp overlay must not survive the request
            self.assertEqual(list(self.dir.glob(".*emlsp*")), [])

    def test_positions_on_the_wire_are_utf16(self):
        source = 'x = "🙂"\nprint(x)\n'
        path = self.dir / "emoji.rald"
        path.write_text(source)
        with self.session() as client:
            uri = client.open(path, source)
            tokens = client.request(
                "textDocument/semanticTokens/full", {"textDocument": {"uri": uri}}
            )
            # line 0: `x`(1) `=`(1) then the string, which is 4 UTF-16 units
            # ("🙂" is a surrogate pair) even though it is 3 characters
            lengths = tokens["data"][2::5]
            self.assertEqual(lengths[:3], [1, 1, 4])

    def test_initialization_options_configure_the_compiler(self):
        os.environ.pop("EMERALDC", None)
        client = Session(self.dir)
        try:
            client.request(
                "initialize",
                {
                    "processId": None,
                    "rootUri": self.dir.as_uri(),
                    "capabilities": {},
                    "initializationOptions": {
                        "emerald": {"compilerPath": self.binary, "debounceMs": 10}
                    },
                },
            )
            client.notify("initialized", {})
            client.open(self.main)
            params = client.wait_for("textDocument/publishDiagnostics")
            self.assertEqual(len(params["diagnostics"]), 1)
        finally:
            client.__exit__()

    def test_closing_a_document_clears_its_diagnostics(self):
        with self.session(with_compiler=True) as client:
            uri = client.open(self.main)
            client.wait_for("textDocument/publishDiagnostics")
            client.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
            params = client.wait_for("textDocument/publishDiagnostics")
            self.assertEqual(params["diagnostics"], [])


if __name__ == "__main__":
    unittest.main()
