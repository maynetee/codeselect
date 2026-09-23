"""Tests for codeselect, standard library only: python3 -m unittest discover tests"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import codeselect  # noqa: E402


def make_project(files, gitignore=None):
    """A temporary project with the given relative files (and an optional .gitignore)."""
    root = tempfile.mkdtemp(prefix="codeselect-")
    for rel, content in files.items():
        path = Path(root, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    if gitignore is not None:
        Path(root, ".gitignore").write_text(gitignore, encoding="utf-8")
    return root


def tree_files(root_node):
    """Relative paths of the files kept in the tree."""
    prefix = root_node.name + os.sep
    return sorted(node.path[len(prefix):].replace(os.sep, "/")
                  for node, _ in codeselect.flatten_tree(root_node, visible_only=False)
                  if not node.is_dir)


class GitignoreSemantics(unittest.TestCase):
    def build(self, files, gitignore):
        root = make_project(files, gitignore)
        patterns = ['.git', '__pycache__', '*.pyc'] + codeselect.read_gitignore(Path(root, ".gitignore"), [])
        return tree_files(codeselect.build_file_tree(root, patterns))

    def test_negation_reincludes_instead_of_excluding_everything(self):
        files = {"a.log": "", "keep.log": "", "main.py": "", "src/util.py": ""}
        self.assertEqual(self.build(files, "*.log\n!keep.log\n"),
                         [".gitignore", "keep.log", "main.py", "src/util.py"])

    def test_a_lone_negation_ignores_nothing(self):
        files = {"main.py": "", "src/util.py": ""}
        self.assertEqual(self.build(files, "!main.py\n"), [".gitignore", "main.py", "src/util.py"])

    def test_directory_pattern_works_from_any_working_directory(self):
        files = {"main.js": "", "node_modules/pkg/index.js": ""}
        elsewhere = tempfile.mkdtemp()
        cwd = os.getcwd()
        try:
            os.chdir(elsewhere)
            self.assertEqual(self.build(files, "node_modules/\n"), [".gitignore", "main.js"])
        finally:
            os.chdir(cwd)

    def test_directory_pattern_does_not_match_a_file(self):
        files = {"build": "a file named build", "src/app.py": ""}
        self.assertEqual(self.build(files, "build/\n"), [".gitignore", "build", "src/app.py"])

    def test_leading_slash_anchors_to_the_root(self):
        files = {"build/out.txt": "", "src/build/keep.txt": ""}
        self.assertEqual(self.build(files, "/build\n"), [".gitignore", "src/build/keep.txt"])

    def test_last_matching_pattern_wins(self):
        self.assertTrue(codeselect.is_ignored("x.log", ["!x.log", "*.log"]))
        self.assertFalse(codeselect.is_ignored("x.log", ["*.log", "!x.log"]))


class DependencyAnalysis(unittest.TestCase):
    def analyse(self, files):
        root = make_project(files)
        node = codeselect.build_file_tree(root)
        contents = codeselect.collect_all_content(node, root)
        return node, contents, codeselect.analyze_dependencies(root, contents)

    def test_from_import_does_not_turn_imported_names_into_dependencies(self):
        _, _, deps = self.analyse({"app.py": "from models import User\nimport os\n",
                                   "models.py": "class User: pass\n"})
        app = next(k for k in deps if k.endswith("app.py"))
        self.assertNotIn("User", deps[app])
        self.assertIn("os", deps[app])
        self.assertTrue(any(d.endswith("models.py") for d in deps[app]))

    def test_package_import_resolves_to_the_package(self):
        _, _, deps = self.analyse({"main.py": "from src import util\n", "src/util.py": "def f(): pass\n"})
        main = next(k for k in deps if k.endswith("main.py"))
        self.assertNotIn("src", deps[main])
        self.assertTrue(any(d.endswith("util.py") for d in deps[main]))

    def test_indented_imports_are_found(self):
        _, _, deps = self.analyse({"app.py": "def f():\n    import json\n    return json\n"})
        app = next(k for k in deps if k.endswith("app.py"))
        self.assertIn("json", deps[app])

    def test_unresolved_import_with_a_slash_is_external(self):
        node, contents, deps = self.analyse({
            "src/main.js": "import React from 'react';\nimport { createRoot } from 'react-dom/client';\n"
                           "import App from './App';\n",
            "src/App.js": "export default function App() {}\n"})
        out = Path(tempfile.mkdtemp(), "out.txt")
        codeselect.write_llm_optimized_output(str(out), "proj", node, contents, deps)
        text = out.read_text(encoding="utf-8")
        main_block = text.split("### Dependencies by File", 1)[1].split("## ", 1)[0]
        internal_line = next(l for l in main_block.splitlines() if "Internal dependencies" in l)
        external_line = next(l for l in main_block.splitlines() if "External dependencies" in l)
        self.assertIn("App.js", internal_line)
        self.assertNotIn("react-dom/client", internal_line)
        self.assertIn("react-dom/client", external_line)

    def test_main_components_count_their_files(self):
        node, contents, deps = self.analyse({"src/a.py": "", "src/b.py": "", "docs/x.md": ""})
        out = Path(tempfile.mkdtemp(), "out.txt")
        codeselect.write_llm_optimized_output(str(out), "proj", node, contents, deps)
        text = out.read_text(encoding="utf-8")
        self.assertIn("**`src/`** - Contains 2 files", text)
        self.assertIn("**`docs/`** - Contains 1 files", text)


if __name__ == "__main__":
    unittest.main()
