import os
import tempfile
import textwrap
import unittest
from pathlib import Path

from emerald_lsp import compiler

# A stand-in for emeraldc: reports one error naming the file it was given, so
# tests can tell the overlay copy from the real file.
FAKE = """\
#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
if "--version" in args:
    print("emeraldc 0.0-fake"); raise SystemExit(0)
target = [a for a in args if a.endswith(".rald")][-1]
roots = [args[i + 1] for i, a in enumerate(args) if a == "-I"]
text = open(target).read()
if "clean" in text:
    print("[]"); raise SystemExit(0)
print(json.dumps([{ "kind": "type", "severity": "error", "code": "E_TYPE_ARG",
    "file": target, "line": 1, "column": 1, "message": "roots=" + ",".join(roots),
    "source_line": text.splitlines()[0] if text else ""}]))
"""


def write_fake(directory: Path) -> str:
    path = directory / "fake-emeraldc"
    path.write_text(FAKE)
    path.chmod(0o755)
    return str(path)


class TestSettings(unittest.TestCase):
    def test_defaults(self):
        settings = compiler.Settings.from_object(None)
        self.assertTrue(settings.diagnostics_enabled)
        self.assertIsNone(settings.compiler_path)

    def test_reads_the_emerald_key(self):
        settings = compiler.Settings.from_object(
            {"emerald": {"compilerPath": "/x/emeraldc", "includePaths": ["/lib"],
                         "proof": True, "debounceMs": 50,
                         "diagnostics": {"enabled": False}}}
        )
        self.assertEqual(settings.compiler_path, "/x/emeraldc")
        self.assertEqual(settings.include_paths, ["/lib"])
        self.assertTrue(settings.proof)
        self.assertEqual(settings.debounce_ms, 50)
        self.assertFalse(settings.diagnostics_enabled)

    def test_accepts_settings_without_the_wrapper(self):
        self.assertEqual(
            compiler.Settings.from_object({"includePaths": ["/lib"]}).include_paths,
            ["/lib"],
        )


class TestLockfile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        os.environ["EMERALD_HOME"] = str(self.dir / "home")
        self.addCleanup(os.environ.pop, "EMERALD_HOME", None)

    def write_lock(self, body: str) -> Path:
        lock = self.dir / compiler.LOCKFILE
        lock.write_text(textwrap.dedent(body))
        return lock

    def test_dependencies_come_before_dependents(self):
        lock = self.write_lock(
            """
            [[package]]
            name = "web"
            version = "1.0.0"
            dependencies = ["http"]

            [[package]]
            name = "http"
            version = "0.2.0"
            """
        )
        roots = compiler.lock_include_paths(lock)
        self.assertEqual(
            [Path(r).parent.name for r in roots], ["http-0.2.0", "web-1.0.0"]
        )
        self.assertTrue(all(r.endswith("src") for r in roots))

    def test_path_dependencies_stay_where_they_are(self):
        lock = self.write_lock(
            """
            [[package]]
            name = "local"
            version = "0.1.0"
            path = "vendor/local"
            """
        )
        self.assertEqual(
            compiler.lock_include_paths(lock), [str(self.dir / "vendor/local/src")]
        )

    def test_a_cycle_does_not_hang(self):
        lock = self.write_lock(
            """
            [[package]]
            name = "a"
            version = "1.0.0"
            dependencies = ["b"]

            [[package]]
            name = "b"
            version = "1.0.0"
            dependencies = ["a"]
            """
        )
        self.assertEqual(len(compiler.lock_include_paths(lock)), 2)

    def test_an_unreadable_lockfile_yields_no_roots(self):
        lock = self.write_lock("this is not toml [[[")
        self.assertEqual(compiler.lock_include_paths(lock), [])

    def test_find_lockfile_walks_up(self):
        nested = self.dir / "src" / "deep"
        nested.mkdir(parents=True)
        lock = self.write_lock("[[package]]\nname='a'\nversion='1.0.0'\n")
        self.assertEqual(compiler.find_lockfile(str(nested / "f.rald")), lock)

    def test_include_paths_deduplicate_and_expand(self):
        settings = compiler.Settings(include_paths=["~/x", "~/x"])
        roots = compiler.include_paths_for(str(self.dir / "f.rald"), settings)
        self.assertEqual(roots, [os.path.expanduser("~/x")])


class TestCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.binary = write_fake(self.dir)
        self.settings = compiler.Settings(compiler_path=self.binary)
        self.file = self.dir / "main.rald"
        self.file.write_text("print(1)\n")

    def test_a_clean_file_has_no_diagnostics(self):
        self.file.write_text("clean\n")
        result = compiler.check(str(self.file), None, self.settings)
        self.assertTrue(result.ok)
        self.assertEqual(result.diagnostics, [])

    def test_diagnostics_are_returned_verbatim(self):
        result = compiler.check(str(self.file), None, self.settings)
        self.assertEqual(result.diagnostics[0]["code"], "E_TYPE_ARG")

    def test_an_unsaved_buffer_is_checked_and_reported_against_the_real_file(self):
        result = compiler.check(str(self.file), "dirty buffer\n", self.settings)
        self.assertEqual(result.diagnostics[0]["file"], str(self.file))
        self.assertEqual(result.diagnostics[0]["source_line"], "dirty buffer")

    def test_the_overlay_file_is_cleaned_up(self):
        compiler.check(str(self.file), "dirty\n", self.settings)
        self.assertEqual(
            [p.name for p in self.dir.glob(".*emlsp*")], [], "temp overlay leaked"
        )

    def test_the_overlay_sits_beside_the_real_file_so_imports_resolve(self):
        temp = compiler._overlay_path(str(self.file))
        self.assertEqual(temp.parent, self.file.parent)
        self.assertTrue(temp.name.startswith("."))

    def test_include_paths_are_passed_as_I_flags(self):
        settings = compiler.Settings(compiler_path=self.binary, include_paths=["/lib"])
        result = compiler.check(str(self.file), None, settings)
        self.assertEqual(result.diagnostics[0]["message"], "roots=/lib")

    def test_a_missing_compiler_is_reported_not_raised(self):
        settings = compiler.Settings(compiler_path=str(self.dir / "nope"))
        with self.assertRaises(compiler.CompilerNotFound):
            compiler.find_compiler(settings)

    def test_garbage_on_stdout_is_not_fatal(self):
        broken = self.dir / "broken"
        broken.write_text("#!/bin/sh\necho not json\n")
        broken.chmod(0o755)
        result = compiler.check(
            str(self.file), None, compiler.Settings(compiler_path=str(broken))
        )
        self.assertFalse(result.ok)
        self.assertIn("unparseable", result.detail)

    def test_probe_reports_the_version(self):
        self.assertEqual(compiler.probe(self.binary), "emeraldc 0.0-fake")


if __name__ == "__main__":
    unittest.main()
