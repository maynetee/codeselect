#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CodeSelect - Easily select files to share with AI assistants

A simple tool that generates a file tree and extracts the content of selected files
to share with AI assistants like Claude or ChatGPT.
"""

import os
import sys
import re
import argparse
import fnmatch
import curses
import shutil
import tempfile
import subprocess
import platform
from pathlib import Path
import datetime
import shlex
import time

__version__ = "1.0.0"

# Cache global pour stocker le contenu formaté
_content_cache = {
    'formatted_content': None,
    'file_contents': None,
    'is_valid': False
}

def read_non_comments(path: Path) -> list[str]:
    """Read non-empty non-comment lines from that path"""
    if not path.is_file():
        return []
    
    try:
        all_lines = path.read_text(encoding='utf-8').splitlines()
        return [line for line in all_lines if line and not line.startswith('#')]
    except (IOError, UnicodeDecodeError, PermissionError):
        return []

def matches_gitignore_pattern(path: str, pattern: str, is_dir=None) -> bool:
    """Check if a path (relative to the project root) matches a gitignore pattern.

    A leading '!' is ignored here: negation is decided by `is_ignored`, where the
    last matching pattern wins. `is_dir` tells whether `path` is a directory; when
    it is None, the path is looked up on disk (only right if `path` is absolute or
    relative to the current directory).
    """
    if pattern.startswith('!'):
        pattern = pattern[1:]

    # Check if pattern is for directories only
    dir_only = pattern.endswith('/')
    if dir_only:
        pattern = pattern[:-1]
        if is_dir is None:
            is_dir = os.path.isdir(path)
        if not is_dir:
            return False
    
    # Normalize path separators
    norm_path = path.replace('\\', '/')
    norm_pattern = pattern.replace('\\', '/')
    
    # Handle patterns with leading /
    if norm_pattern.startswith('/'):
        # Leading / anchors the pattern to the project root: "/build" matches
        # "build" but not "src/build"
        return fnmatch.fnmatch(norm_path, norm_pattern[1:])
    
    # Handle ** pattern (matches any directory depth)
    if '**' in norm_pattern:
        # Split the pattern at **
        parts = norm_pattern.split('**')
        
        if len(parts) == 2:
            # Simple case: **/pattern or pattern/**
            if parts[0] == '':
                # **/pattern - match any path ending with pattern
                return norm_path.endswith(parts[1])
            elif parts[1] == '':
                # pattern/** - match any path starting with pattern
                return norm_path.startswith(parts[0])
            else:
                # pattern1**/pattern2 - match if contains both patterns in order
                return parts[0] in norm_path and parts[1] in norm_path and norm_path.index(parts[0]) < norm_path.index(parts[1])
        else:
            # More complex case with multiple **
            regex_pattern = norm_pattern.replace('.', '\\.').replace('**/','.*').replace('**','.*').replace('*','[^/]*').replace('?','[^/]')
            return re.match(f"^{regex_pattern}$", norm_path) is not None
    
    # Standard glob pattern matching
    return (
        fnmatch.fnmatch(norm_path, norm_pattern) or 
        fnmatch.fnmatch(os.path.basename(norm_path), norm_pattern) or
        any(fnmatch.fnmatch(part, norm_pattern) for part in norm_path.split('/'))
    )

def is_ignored(rel_path: str, patterns: list[str], is_dir: bool = False) -> bool:
    """Apply ignore patterns with gitignore semantics: patterns are read in order,
    the last one that matches decides, and '!pattern' re-includes a path."""
    ignored = False
    for pattern in patterns:
        negated = pattern.startswith('!')
        body = pattern[1:] if negated else pattern
        if not body:
            continue
        if matches_gitignore_pattern(rel_path, body, is_dir=is_dir):
            ignored = not negated
    return ignored

def read_gitignore(path: Path, base_patterns: list[str]) -> list[str]:
    """Read gitignore patterns from a file."""
    lines = read_non_comments(path)
    return [line.rstrip() for line in lines if line and line.rstrip() not in base_patterns]

# Structure to represent a node in the file tree
class Node:
    def __init__(self, name, is_dir, parent=None):
        self.name = name
        self.is_dir = is_dir
        self.children = {} if is_dir else None
        self.parent = parent
        self.selected = True  # Selected by default
        self.expanded = True  # Folders expanded by default

    @property
    def path(self):
        """Get the full path of the node."""
        if self.parent is None:
            return self.name
        parent_path = self.parent.path
        if parent_path.endswith(os.sep):
            return parent_path + self.name
        return parent_path + os.sep + self.name

def build_file_tree(root_path, ignore_patterns=None):
    """Build a tree representing the file structure."""
    if ignore_patterns is None:
        # Default base patterns to ignore
        base_patterns = ['.git', '__pycache__', '*.pyc', '.DS_Store', '.idea', '.vscode']
        
        # Add global gitignore patterns
        global_ignore_patterns = []
        global_gitignore_paths = [
            Path('~/.gitignore_global').expanduser(),  # Standard global gitignore
            Path('~/.config/git/ignore').expanduser(),  # Git's built-in global ignore
            Path('~/.gitignore').expanduser(),         # Alternative location
        ]
        
        for global_path in global_gitignore_paths:
            if global_path.exists():
                patterns = read_gitignore(global_path, base_patterns)
                global_ignore_patterns.extend(patterns)
        
        # Add local gitignore patterns
        local_gitignore_path = Path(root_path) / '.gitignore'
        local_ignore_patterns = read_gitignore(local_gitignore_path, base_patterns)
        
        # Also respect project gitignore file
        project_gitignore_path = Path(root_path) / '.projectignore'
        project_ignore_patterns = read_gitignore(project_gitignore_path, base_patterns)
        
        # Combine all patterns
        ignore_patterns = base_patterns + global_ignore_patterns + local_ignore_patterns + project_ignore_patterns

    def should_ignore(path):
        """Check if a path should be ignored."""
        rel_path = os.path.relpath(path, root_path)
        rel_path = rel_path.replace('\\', '/') # Normalize path separators for pattern matching
        
        # Special case for symlinks to avoid infinite recursion
        if os.path.islink(path):
            return True

        return is_ignored(rel_path, ignore_patterns, is_dir=os.path.isdir(path))

    root_name = os.path.basename(root_path.rstrip(os.sep))
    if not root_name:  # Case for root directory
        root_name = root_path

    root_node = Node(root_name, True)
    root_node.full_path = root_path  # Store absolute path for root

    def add_path(current_node, path_parts, full_path):
        if not path_parts:
            return

        part = path_parts[0]
        remaining = path_parts[1:]

        if should_ignore(os.path.join(full_path, part)):
            return

        # Check if part already exists
        if part in current_node.children:
            child = current_node.children[part]
        else:
            is_dir = os.path.isdir(os.path.join(full_path, part))
            child = Node(part, is_dir, current_node)
            current_node.children[part] = child

        # If there are remaining parts, continue recursively
        if remaining:
            next_path = os.path.join(full_path, part)
            add_path(child, remaining, next_path)

    # Walk the directory structure
    for dirpath, dirnames, filenames in os.walk(root_path, topdown=True, followlinks=False):
        # Skip filtered directories
        dirnames[:] = [d for d in dirnames if not should_ignore(os.path.join(dirpath, d))]

        rel_path = os.path.relpath(dirpath, root_path)
        if rel_path == '.':
            # Add files in root
            for filename in filenames:
                if filename not in root_node.children and not should_ignore(os.path.join(dirpath, filename)):
                    file_node = Node(filename, False, root_node)
                    root_node.children[filename] = file_node
        else:
            # Add the directory
            path_parts = rel_path.split(os.sep)
            add_path(root_node, path_parts, root_path)

            # Add files in this directory
            current = root_node
            for part in path_parts:
                if part in current.children:
                    current = current.children[part]
                else:
                    # Skip if directory was filtered
                    break
            else:
                for filename in filenames:
                    if not should_ignore(os.path.join(dirpath, filename)) and filename not in current.children:
                        file_node = Node(filename, False, current)
                        current.children[filename] = file_node

    return root_node

def flatten_tree(node, visible_only=True):
    """Flatten the tree into a list of nodes for navigation."""
    flat_nodes = []

    def _traverse(node, level=0):
        if node.parent is not None:  # Skip root node
            flat_nodes.append((node, level))

        if node.is_dir and node.children and (not visible_only or node.expanded):
            # Sort directories first, then files, then alphabetically
            items = sorted(node.children.items(),
                          key=lambda x: (not x[1].is_dir, x[0].lower()))

            for _, child in items:
                _traverse(child, level + 1)

    _traverse(node)
    return flat_nodes

def count_selected_files(node):
    """Count the number of selected files (not directories)."""
    count = 0
    if not node.is_dir and node.selected:
        count = 1
    elif node.is_dir and node.children:
        for child in node.children.values():
            count += count_selected_files(child)
    return count

def collect_selected_content(node, root_path):
    """Collect content from selected files."""
    results = []

    if not node.is_dir and node.selected:
        file_path = node.path

        # Ensure we're not duplicating the root path
        if node.parent and node.parent.parent is None:
            # If the node is directly under root, use just the filename
            full_path = os.path.join(root_path, node.name)
        else:
            # For nested files, construct proper relative path
            rel_path = file_path
            if file_path.startswith(os.path.basename(root_path) + os.sep):
                rel_path = file_path[len(os.path.basename(root_path) + os.sep):]
            full_path = os.path.join(root_path, rel_path)

        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()
            results.append((file_path, content))
        except UnicodeDecodeError:
            print(f"Ignoring binary file: {file_path}")
        except Exception as e:
            print(f"Error reading {full_path}: {e}")
    elif node.is_dir and node.children:
        for child in node.children.values():
            results.extend(collect_selected_content(child, root_path))

    return results

def collect_all_content(node, root_path):
    """Collect content from all files (for analysis)."""
    results = []

    if not node.is_dir:
        file_path = node.path

        # Apply the same path fixes as in collect_selected_content
        if node.parent and node.parent.parent is None:
            full_path = os.path.join(root_path, node.name)
        else:
            rel_path = file_path
            if file_path.startswith(os.path.basename(root_path) + os.sep):
                rel_path = file_path[len(os.path.basename(root_path) + os.sep):]
            full_path = os.path.join(root_path, rel_path)

        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()
            results.append((file_path, content))
        except UnicodeDecodeError:
            pass  # Silently ignore binary files
        except Exception:
            pass  # Silently ignore errors
    elif node.is_dir and node.children:
        for child in node.children.values():
            results.extend(collect_all_content(child, root_path))

    return results

def analyze_dependencies(root_path, file_contents):
    """Analyze relationships between project files."""
    dependencies = {}
    imports = {}

    # Define detection patterns for different languages
    language_patterns = {
        # Python
        '.py': [
            r'^\s*from\s+([\w.]+)\s+import',
            r'^\s*import\s+([\w.]+)',
        ],
        # C/C++
        '.c': [r'#include\s+[<"]([^>"]+)[>"]'],
        '.h': [r'#include\s+[<"]([^>"]+)[>"]'],
        '.cpp': [r'#include\s+[<"]([^>"]+)[>"]'],
        '.hpp': [r'#include\s+[<"]([^>"]+)[>"]'],
        # JavaScript/TypeScript
        '.js': [
            r'(?:import|require)\s*\(?[\'"]([@\w\-./]+)[\'"]',
            r'from\s+[\'"]([@\w\-./]+)[\'"]',
            r'import\s*\{[^}]*\}\s*from\s*[\'"]([^\'")]+)[\'"]',  # Named imports
        ],
        '.ts': [
            r'(?:import|require)\s*\(?[\'"]([@\w\-./]+)[\'"]',
            r'from\s+[\'"]([@\w\-./]+)[\'"]',
            r'import\s*\{[^}]*\}\s*from\s*[\'"]([^\'")]+)[\'"]',  # Named imports
            r'import\s+type\s+\{[^}]*\}\s*from\s*[\'"]([^\'")]+)[\'"]',  # Type imports
        ],
        '.jsx': [
            r'(?:import|require)\s*\(?[\'"]([@\w\-./]+)[\'"]',
            r'from\s+[\'"]([@\w\-./]+)[\'"]',
            r'import\s*\{[^}]*\}\s*from\s*[\'"]([^\'")]+)[\'"]',  # Named imports
        ],
        '.tsx': [
            r'(?:import|require)\s*\(?[\'"]([@\w\-./]+)[\'"]',
            r'from\s+[\'"]([@\w\-./]+)[\'"]',
            r'import\s*\{[^}]*\}\s*from\s*[\'"]([^\'")]+)[\'"]',  # Named imports
            r'import\s+type\s+\{[^}]*\}\s*from\s*[\'"]([^\'")]+)[\'"]',  # Type imports
        ],
        # Java
        '.java': [
            r'import\s+([\w.]+)',
            r'import\s+static\s+([\w.]+)',
        ],
        # Go
        '.go': [
            r'import\s+\(\s*(?:[_\w]*\s+)?["]([^"]+)["]',
            r'import\s+(?:[_\w]*\s+)?["]([^"]+)["]',
        ],
        # Ruby
        '.rb': [
            r'require\s+[\'"]([^\'"]+)[\'"]',
            r'require_relative\s+[\'"]([^\'"]+)[\'"]',
            r'load\s+[\'"]([^\'"]+)[\'"]',
        ],
        # PHP
        '.php': [
            r'(?:require|include|require_once|include_once)\s*\(?[\'"]([^\'"]+)[\'"]',
            r'use\s+([\w\\]+)',
            r'namespace\s+([\w\\]+)',
        ],
        # Rust
        '.rs': [
            r'use\s+([\w:]+)',
            r'extern\s+crate\s+([\w]+)',
            r'mod\s+(\w+)',
        ],
        # Swift
        '.swift': [
            r'import\s+(\w+)',
            r'@testable\s+import\s+(\w+)',
        ],
        # Shell scripts
        '.sh': [
            r'source\s+[\'"]?([^\'"]+)[\'"]?',
            r'\.\s+[\'"]?([^\'"]+)[\'"]?',
        ],
        # Makefile
        'Makefile': [
            r'include\s+([^\s]+)',
        ],
        # Kotlin
        '.kt': [
            r'import\s+([\w.]+)',
            r'package\s+([\w.]+)',
        ],
        # Dart/Flutter
        '.dart': [
            r'import\s+[\'"]([^\'"]+)[\'"]',
            r'part\s+[\'"]([^\'"]+)[\'"]',
            r'export\s+[\'"]([^\'"]+)[\'"]',
        ],
    }

    # First pass: collect all imports
    for file_path, content in file_contents:
        dependencies[file_path] = set()
        imports[file_path] = set()

        ext = os.path.splitext(file_path)[1].lower()
        basename = os.path.basename(file_path)

        # Select appropriate patterns
        patterns = []
        if ext in language_patterns:
            patterns = language_patterns[ext]
        elif basename in language_patterns:
            patterns = language_patterns[basename]

        # Apply all relevant patterns
        for pattern in patterns:
            matches = re.findall(pattern, content, re.MULTILINE)
            for match in matches:
                if isinstance(match, tuple):  # Some regex groups return tuples
                    for m in match:
                        if m:  # Skip empty matches
                            imports[file_path].add(m)
                else:
                    imports[file_path].add(match)

    # Second pass: resolve references between files
    file_mapping = {}  # Create mapping of possible names to file paths

    for file_path, _ in file_contents:
        basename = os.path.basename(file_path)
        name_without_ext = os.path.splitext(basename)[0]

        # Add different forms of file name
        file_mapping[basename] = file_path
        file_mapping[name_without_ext] = file_path
        file_mapping[file_path] = file_path

        # For paths with folders, also add relative variants
        if os.path.dirname(file_path):
            rel_path = file_path
            rel_path_no_ext = os.path.splitext(rel_path)[0]
            file_mapping[rel_path_no_ext] = file_path
            
            # Handle directory paths for package imports ("from src import util")
            dir_path = os.path.dirname(file_path)
            file_mapping[dir_path] = file_path
            while '/' in dir_path:
                dir_path = dir_path[dir_path.find('/')+1:]
                file_mapping.setdefault(dir_path, file_path)
            
            # Handle path variations
            while '/' in rel_path:
                rel_path = rel_path[rel_path.find('/')+1:]
                file_mapping[rel_path] = file_path
                file_mapping[os.path.splitext(rel_path)[0]] = file_path

    # Resolve imports to file dependencies
    for file_path, imported in imports.items():
        for imp in imported:
            # Try to match import with a known file
            matched = False

            # Try variations of the import to find a match
            import_variations = [
                imp,
                os.path.basename(imp),
                os.path.splitext(imp)[0],
                imp.replace('.', '/'),
                imp.replace('.', '/') + '.py',  # For Python
                imp + '.h',  # For C
                imp + '.hpp',  # For C++
                imp + '.js',  # For JS
                imp + '.jsx',  # For React
                imp + '.ts',  # For TypeScript
                imp + '.tsx',  # For React+TypeScript
                imp + '.java',  # For Java
                imp + '.kt',  # For Kotlin
                imp + '.dart',  # For Dart
                imp + '.go',  # For Go
                imp + '.rb',  # For Ruby
                imp + '.php',  # For PHP
                imp + '.rs',  # For Rust
                imp + '.swift',  # For Swift
                imp + '.sh',  # For Shell
                './'+imp,  # Relative imports
                '../'+imp,  # Parent imports
            ]
            
            # Add package-style variations (for JS/TS/Python)
            package_parts = imp.split('.')
            if len(package_parts) > 1:
                # Add partial matches for submodules
                for i in range(1, len(package_parts)):
                    import_variations.append('/'.join(package_parts[:i]))
                    
                # Try directory with index/init files
                package_path = imp.replace('.', '/')
                import_variations.extend([
                    f"{package_path}/index.js",
                    f"{package_path}/index.ts",
                    f"{package_path}/index.jsx",
                    f"{package_path}/index.tsx",
                    f"{package_path}/__init__.py"
                ])

            for var in import_variations:
                if var in file_mapping:
                    dependencies[file_path].add(file_mapping[var])
                    matched = True
                    break

            # If no match found, keep the import as is
            if not matched:
                dependencies[file_path].add(imp)

    return dependencies

def write_llm_optimized_output(output_path, root_path, root_node, file_contents, dependencies):
    """Write output in a format optimized for LLM analysis."""
    with open(output_path, 'w', encoding='utf-8') as f:
        # Header and overview
        f.write("# PROJECT ANALYSIS FOR AI ASSISTANT\n\n")

        # General project information
        total_files = sum(1 for node, _ in flatten_tree(root_node) if not node.is_dir)
        selected_files = count_selected_files(root_node)
        f.write("## 📦 GENERAL INFORMATION\n\n")
        f.write(f"- **Project path**: `{root_path}`\n")
        f.write(f"- **Total files**: {total_files}\n")
        f.write(f"- **Files included in this analysis**: {selected_files}\n")

        # Detect languages used
        languages = {}
        for path, _ in file_contents:
            ext = os.path.splitext(path)[1].lower()
            if ext:
                ext = ext[1:]  # Remove the dot
                languages[ext] = languages.get(ext, 0) + 1

        if languages:
            f.write("- **Main languages used**:\n")
            for ext, count in sorted(languages.items(), key=lambda x: x[1], reverse=True)[:5]:
                lang_name = get_language_name(ext)
                f.write(f"  - {lang_name} ({count} files)\n")
        f.write("\n")

        # Project structure
        f.write("## 🗂️ PROJECT STRUCTURE\n\n")
        f.write("```\n")
        f.write(f"{root_path}\n")
        f.write(write_file_tree_to_string(root_node))
        f.write("```\n\n")

        # Main directories and components
        main_dirs = [node for node, level in flatten_tree(root_node, False)
                    if node.is_dir and level == 1]

        if main_dirs:
            f.write("### 📂 Main Components\n\n")
            for dir_node in main_dirs:
                prefix = os.path.join(root_node.name, dir_node.name) + os.sep
                dir_files = [p for p, _ in file_contents if p.startswith(prefix)]
                f.write(f"- **`{dir_node.name}/`** - ")
                if dir_files:
                    f.write(f"Contains {len(dir_files)} files")

                    # Languages in this directory
                    dir_exts = {}
                    for path in dir_files:
                        ext = os.path.splitext(path)[1].lower()
                        if ext:
                            ext = ext[1:]
                            dir_exts[ext] = dir_exts.get(ext, 0) + 1

                    if dir_exts:
                        main_langs = [get_language_name(ext) for ext, _ in
                                     sorted(dir_exts.items(), key=lambda x: x[1], reverse=True)[:2]]
                        f.write(f" mainly in {', '.join(main_langs)}")

                f.write("\n")
            f.write("\n")

        # File relationship graph
        f.write("## 🔄 FILE RELATIONSHIPS\n\n")

        # A dependency is internal when it is one of the analysed files; anything
        # else (e.g. "react-dom/client", "os") is external, whatever its shape
        project_files = {p for p, _ in file_contents} | set(dependencies)

        # Find most referenced files
        referenced_by = {}
        for file, deps in dependencies.items():
            for dep in deps:
                if dep in project_files:
                    if dep not in referenced_by:
                        referenced_by[dep] = []
                    referenced_by[dep].append(file)

        # Display important relationships
        if referenced_by:
            f.write("### Core Files (most referenced)\n\n")
            for file, refs in sorted(referenced_by.items(), key=lambda x: len(x[1]), reverse=True)[:10]:
                if len(refs) > 1:  # Only files referenced multiple times
                    f.write(f"- **`{file}`** is imported by {len(refs)} files\n")
            f.write("\n")

        # Display dependencies per file
        f.write("### Dependencies by File\n\n")
        for file, deps in sorted(dependencies.items()):
            if deps:
                internal_deps = [d for d in deps if d in project_files]
                external_deps = [d for d in deps if d not in project_files]

                f.write(f"- **`{file}`**:\n")

                if internal_deps:
                    f.write(f"  - *Internal dependencies*: ")
                    f.write(", ".join(f"`{d}`" for d in sorted(internal_deps)[:5]))
                    if len(internal_deps) > 5:
                        f.write(f" and {len(internal_deps)-5} more")
                    f.write("\n")

                if external_deps:
                    f.write(f"  - *External dependencies*: ")
                    f.write(", ".join(f"`{d}`" for d in sorted(external_deps)[:5]))
                    if len(external_deps) > 5:
                        f.write(f" and {len(external_deps)-5} more")
                    f.write("\n")
        f.write("\n")

        # File contents
        f.write("## 📄 FILE CONTENTS\n\n")
        f.write("*Note: The content below includes only selected files.*\n\n")

        for path, content in file_contents:
            f.write(f"### {path}\n\n")

            # Add file info if available
            file_deps = dependencies.get(path, set())
            if file_deps:
                internal_deps = [d for d in file_deps if d in project_files]
                external_deps = [d for d in file_deps if d not in project_files]

                if internal_deps or external_deps:
                    f.write("**Dependencies:**\n")

                    if internal_deps:
                        f.write("- Internal: " + ", ".join(f"`{d}`" for d in sorted(internal_deps)[:3]))
                        if len(internal_deps) > 3:
                            f.write(f" and {len(internal_deps)-3} more")
                        f.write("\n")

                    if external_deps:
                        f.write("- External: " + ", ".join(f"`{d}`" for d in sorted(external_deps)[:3]))
                        if len(external_deps) > 3:
                            f.write(f" and {len(external_deps)-3} more")
                        f.write("\n")

                    f.write("\n")

            # Syntax highlighting based on extension
            ext = os.path.splitext(path)[1][1:].lower()
            f.write(f"```{ext}\n")
            f.write(content)
            if not content.endswith('\n'):
                f.write('\n')
            f.write("```\n\n")

def get_language_name(extension):
    """Convert a file extension to a language name."""
    language_map = {
        'py': 'Python',
        'c': 'C',
        'cpp': 'C++',
        'h': 'C/C++ Header',
        'hpp': 'C++ Header',
        'js': 'JavaScript',
        'jsx': 'React',
        'ts': 'TypeScript',
        'tsx': 'React TypeScript',
        'java': 'Java',
        'html': 'HTML',
        'css': 'CSS',
        'scss': 'SCSS',
        'sass': 'Sass',
        'less': 'Less',
        'php': 'PHP',
        'rb': 'Ruby',
        'go': 'Go',
        'rs': 'Rust',
        'swift': 'Swift',
        'kt': 'Kotlin',
        'dart': 'Dart',
        'sh': 'Shell',
        'md': 'Markdown',
        'json': 'JSON',
        'xml': 'XML',
        'yaml': 'YAML',
        'yml': 'YAML',
        'sql': 'SQL',
        'r': 'R',
        'vue': 'Vue',
        'svelte': 'Svelte',
    }
    return language_map.get(extension, extension.upper())

# Cache du meilleur outil de clipboard détecté
_best_clipboard_tool = None

def find_best_clipboard_tool():
    """Find the best and fastest clipboard tool for the current platform."""
    global _best_clipboard_tool
    
    # Return cached value if available
    if _best_clipboard_tool is not None:
        return _best_clipboard_tool
    
    # Commandes par plateforme
    commands = []
    
    # Déterminer le système
    system = platform.system().lower()
    
    # macOS
    if system == 'darwin':
        commands = [['pbcopy']]
    # Windows
    elif system == 'windows':
        commands = [['clip']]
    # WSL
    elif 'microsoft' in platform.release().lower() or os.path.exists('/proc/sys/fs/binfmt_misc/WSLInterop'):
        commands = [['clip.exe']]
    # Linux
    else:
        # Wayland
        if os.environ.get('WAYLAND_DISPLAY'):
            commands = [['wl-copy'], ['wl-clipboard']]
        # X11
        else:
            commands = [['xclip', '-selection', 'clipboard'], ['xsel', '-ib']]
    
    # Tester chaque commande avec un petit texte
    test_text = "test"
    
    for cmd in commands:
        try:
            start = time.time()
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            stdout, stderr = process.communicate(test_text.encode('utf-8'), timeout=1)
            
            # Si la commande fonctionne et est rapide
            if process.returncode == 0:
                elapsed = time.time() - start
                # Cacher la commande qui a fonctionné
                _best_clipboard_tool = cmd
                return cmd
        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
            continue
    
    # Retourner la première commande par défaut si aucune ne fonctionne
    if commands:
        _best_clipboard_tool = commands[0]
        return commands[0]
    
    return None

def optimized_copy_to_clipboard(text):
    """Copy text to clipboard using the most optimized method for the current platform."""
    clipboard_tool = find_best_clipboard_tool()
    
    if not clipboard_tool:
        # Fallback en cas d'échec de détection d'outil
        fallback_path = os.path.expanduser("~/codeselect_output.txt")
        with open(fallback_path, 'w', encoding='utf-8') as f:
            f.write(text)
        print(f"Clipboard copy failed. Output saved to: {fallback_path}")
        return False
    
    try:
        # Utiliser directement l'outil le plus rapide (sans vérifications supplémentaires)
        process = subprocess.Popen(
            clipboard_tool,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Utiliser un timeout court pour éviter les blocages
        stdout, stderr = process.communicate(text.encode('utf-8'), timeout=3)
        
        if process.returncode == 0:
            return True
            
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except:
            pass
    except Exception as e:
        pass
    
    # Fallback en cas d'échec
    fallback_path = os.path.expanduser("~/codeselect_output.txt")
    with open(fallback_path, 'w', encoding='utf-8') as f:
        f.write(text)
    print(f"Clipboard copy failed. Output saved to: {fallback_path}")
    return False

def generate_formatted_content(root_path, root_node, output_format='llm'):
    """Generate formatted content string without writing to a file."""
    # Check cache first
    global _content_cache
    if _content_cache['is_valid']:
        return _content_cache['formatted_content'], _content_cache['file_contents']
    
    # Collect content from selected files
    file_contents = collect_selected_content(root_node, root_path)
    
    # Create a temporary file to hold the formatted content
    with tempfile.NamedTemporaryFile(mode='w+', encoding='utf-8', delete=False) as temp_file:
        temp_path = temp_file.name
    
    try:
        # For LLM format, analyze dependencies
        if output_format == 'llm':
            all_files = collect_all_content(root_node, root_path)
            dependencies = analyze_dependencies(root_path, all_files)
            write_llm_optimized_output(temp_path, root_path, root_node, file_contents, dependencies)
        elif output_format == 'md':
            write_markdown_output(temp_path, root_path, root_node, file_contents)
        else:
            # Default txt format
            with open(temp_path, 'w', encoding='utf-8') as f:
                # Write file tree
                f.write("<file_map>\n")
                f.write(f"{root_path}\n")
                tree_str = write_file_tree_to_string(root_node)
                f.write(tree_str)
                f.write("</file_map>\n\n")
                
                # Write file contents
                f.write("<file_contents>\n")
                for path, content in file_contents:
                    f.write(f"File: {path}\n")
                    f.write("```")
                    ext = os.path.splitext(path)[1][1:].lower()
                    if ext:
                        f.write(ext)
                    f.write("\n")
                    f.write(content)
                    if not content.endswith('\n'):
                        f.write('\n')
                    f.write("```\n\n")
                f.write("</file_contents>\n")
        
        # Read the formatted content
        with open(temp_path, 'r', encoding='utf-8') as f:
            formatted_content = f.read()
        
        # Cache the results
        _content_cache['formatted_content'] = formatted_content
        _content_cache['file_contents'] = file_contents
        _content_cache['is_valid'] = True
        
        return formatted_content, file_contents
    
    finally:
        # Clean up the temporary file
        try:
            os.unlink(temp_path)
        except:
            pass

def handle_clipboard_only(root_path, root_node, output_format='llm'):
    """Copy to clipboard without creating a file and exit."""
    print("\nPreparing content for clipboard...")
    
    # Generate content
    formatted_content, file_contents = generate_formatted_content(
        root_path, root_node, output_format
    )
    
    # Copy to clipboard using optimized method
    print(f"Copying {len(file_contents)} files to clipboard ({len(formatted_content)} bytes)...")
    if optimized_copy_to_clipboard(formatted_content):
        print("Content copied to clipboard successfully!")
    else:
        print("Failed to copy to clipboard.")
    
    # Exit the program directly
    sys.exit(0)

class FileSelector:
    def __init__(self, root_node, stdscr, output_format='llm', copy_to_clipboard=True):
        self.root_node = root_node
        self.stdscr = stdscr
        self.current_index = 0
        self.scroll_offset = 0
        self.visible_nodes = flatten_tree(root_node)
        self.max_visible = 0
        self.height, self.width = 0, 0
        self.copy_to_clipboard = copy_to_clipboard  # starts from --no-clipboard, toggled with B
        self.output_format = output_format  # Store the output format
        self.clipboard_only = False  # Default: create file and copy to clipboard
        self.initialize_curses()

    def initialize_curses(self):
        """Initialize curses settings."""
        curses.start_color()
        curses.use_default_colors()
        # Define color pairs
        curses.init_pair(1, curses.COLOR_GREEN, -1)    # Selected files
        curses.init_pair(2, curses.COLOR_BLUE, -1)     # Directories
        curses.init_pair(3, curses.COLOR_YELLOW, -1)   # Selected directories
        curses.init_pair(4, curses.COLOR_WHITE, -1)    # Unselected files
        curses.init_pair(5, curses.COLOR_BLACK, curses.COLOR_WHITE)  # Current selection
        curses.init_pair(6, curses.COLOR_RED, -1)      # Help message

        # Hide cursor
        curses.curs_set(0)

        # Enable special keys
        self.stdscr.keypad(True)

        # Get screen dimensions
        self.update_dimensions()

    def update_dimensions(self):
        """Update screen dimensions."""
        self.height, self.width = self.stdscr.getmaxyx()
        self.max_visible = self.height - 6  # One more line for stats at top

    def expand_all(self, expand=True):
        """Expand or collapse all directories."""
        def _set_expanded(node, expand):
            if node.is_dir and node.children:
                node.expanded = expand
                for child in node.children.values():
                    _set_expanded(child, expand)

        _set_expanded(self.root_node, expand)
        self.visible_nodes = flatten_tree(self.root_node)

    def toggle_current_dir_selection(self):
        """Toggle selection of files in current directory only (no subdirectories)."""
        if self.current_index < len(self.visible_nodes):
            current_node, _ = self.visible_nodes[self.current_index]

            # If current node is a directory, toggle selection of its immediate children only
            if current_node.is_dir and current_node.children:
                # Check if majority of children are selected to determine action
                selected_count = sum(1 for child in current_node.children.values() if child.selected)
                select_all = selected_count <= len(current_node.children) / 2

                # Set all immediate children to the new selection state
                for child in current_node.children.values():
                    child.selected = select_all
            # If current node is a file, just toggle its selection
            else:
                current_node.selected = not current_node.selected
                
            # Invalidate content cache when selection changes
            global _content_cache
            _content_cache['is_valid'] = False

    def draw_tree(self):
        """Draw the file tree."""
        self.stdscr.clear()
        self.update_dimensions()

        # Update visible nodes list
        self.visible_nodes = flatten_tree(self.root_node)

        # Check bounds
        if self.current_index >= len(self.visible_nodes):
            self.current_index = len(self.visible_nodes) - 1
        if self.current_index < 0:
            self.current_index = 0

        # Adjust scroll if needed
        if self.current_index < self.scroll_offset:
            self.scroll_offset = self.current_index
        elif self.current_index >= self.scroll_offset + self.max_visible:
            self.scroll_offset = self.current_index - self.max_visible + 1

        # Display statistics on line 1 (not line 0) to avoid hiding first item
        selected_count = count_selected_files(self.root_node)
        total_count = sum(1 for node, _ in self.visible_nodes if not node.is_dir)
        self.stdscr.addstr(0, 0, f"Selected files: {selected_count}/{total_count}", curses.A_BOLD)

        # Draw visible nodes starting from line 1
        for i, (node, level) in enumerate(self.visible_nodes[self.scroll_offset:self.scroll_offset + self.max_visible]):
            y = i + 1  # Start from line 1 (below stats)
            if y >= self.max_visible + 1:
                break

            # Determine color based on type and selection state
            if i + self.scroll_offset == self.current_index:
                # Active node (highlighted)
                attr = curses.color_pair(5)
            elif node.is_dir:
                # Directory
                attr = curses.color_pair(3) if node.selected else curses.color_pair(2)
            else:
                # File
                attr = curses.color_pair(1) if node.selected else curses.color_pair(4)

            # Prepare line to display
            indent = "  " * level
            if node.is_dir:
                prefix = "+ " if node.expanded else "- "
            else:
                prefix = "✓ " if node.selected else "☐ "

            # Truncate name if too long
            name_space = self.width - len(indent) - len(prefix) - 2
            name_display = node.name[:name_space] + ("..." if len(node.name) > name_space else "")

            # Display the line
            self.stdscr.addstr(y, 0, f"{indent}{prefix}{name_display}", attr)

        # Display help at bottom of screen
        help_y = self.height - 5
        self.stdscr.addstr(help_y, 0, "━" * self.width)
        help_y += 1
        self.stdscr.addstr(help_y, 0, "↑/↓: Navigate  SPACE: Select  ←/→: Close/Open folder", curses.color_pair(6))
        help_y += 1
        self.stdscr.addstr(help_y, 0, "T: Toggle dir only  E: Expand all  C: Collapse all", curses.color_pair(6))
        help_y += 1
        
        # Status indicators
        clip_status = "ON" if self.copy_to_clipboard else "OFF"
        clipo_status = "ON" if self.clipboard_only else "OFF"
        
        help_text = f"A: All  N: None  B: Clipboard({clip_status})  O: ClipboardOnly  D: Done  X: Exit"
        
        # Truncate if it's too long for the screen
        if len(help_text) > self.width:
            help_text = help_text[:self.width-3] + "..."
            
        self.stdscr.addstr(help_y, 0, help_text, curses.color_pair(6))

        self.stdscr.refresh()

    def toggle_selection(self, node):
        """Toggle selection of a node and its children if it's a directory."""
        node.selected = not node.selected

        if node.is_dir and node.children:
            for child in node.children.values():
                child.selected = node.selected
                if child.is_dir:
                    self.toggle_selection(child)
        
        # Invalidate content cache when selection changes
        global _content_cache
        _content_cache['is_valid'] = False

    def toggle_expand(self, node):
        """Expand or collapse a directory."""
        if node.is_dir:
            node.expanded = not node.expanded
            # Update the visible nodes list
            self.visible_nodes = flatten_tree(self.root_node)

    def select_all(self, select=True):
        """Select or deselect all nodes."""
        def _select_recursive(node):
            node.selected = select
            if node.is_dir and node.children:
                for child in node.children.values():
                    _select_recursive(child)

        _select_recursive(self.root_node)
        
        # Invalidate content cache when selection changes
        global _content_cache
        _content_cache['is_valid'] = False

    def run(self, root_path):
        """Run the selection interface."""
        while True:
            self.draw_tree()
            key = self.stdscr.getch()

            if key == curses.KEY_UP:
                # Move up
                self.current_index = max(0, self.current_index - 1)

            elif key == curses.KEY_DOWN:
                # Move down
                self.current_index = min(len(self.visible_nodes) - 1, self.current_index + 1)

            elif key == curses.KEY_RIGHT:
                # Open a directory
                if self.current_index < len(self.visible_nodes):
                    node, _ = self.visible_nodes[self.current_index]
                    if node.is_dir and not node.expanded:
                        self.toggle_expand(node)

            elif key == curses.KEY_LEFT:
                # Close a directory
                if self.current_index < len(self.visible_nodes):
                    node, _ = self.visible_nodes[self.current_index]
                    if node.is_dir and node.expanded:
                        self.toggle_expand(node)
                    elif node.parent and node.parent.parent:  # Go to parent (except root)
                        # Find parent's index
                        for i, (n, _) in enumerate(self.visible_nodes):
                            if n == node.parent:
                                self.current_index = i
                                break

            elif key == ord(' '):
                # Toggle selection
                if self.current_index < len(self.visible_nodes):
                    node, _ = self.visible_nodes[self.current_index]
                    self.toggle_selection(node)

            elif key in [ord('a'), ord('A')]:
                # Select all
                self.select_all(True)

            elif key in [ord('n'), ord('N')]:
                # Select none
                self.select_all(False)

            elif key in [ord('e'), ord('E')]:
                # Expand all
                self.expand_all(True)

            elif key in [ord('c'), ord('C')]:
                # Collapse all
                self.expand_all(False)

            elif key in [ord('t'), ord('T')]:
                # Toggle selection of current directory only
                self.toggle_current_dir_selection()

            elif key in [ord('b'), ord('B')]:
                # Toggle clipboard setting
                self.copy_to_clipboard = not self.copy_to_clipboard

            elif key in [ord('o'), ord('O')]:
                # Sortir complètement pour le clipboard-only
                curses.endwin()
                handle_clipboard_only(root_path, self.root_node, self.output_format)
                # Cette fonction ne revient jamais, elle quitte le programme

            elif key in [ord('x'), ord('X'), 27]:  # 27 = ESC
                # Exit without saving
                return False, None, False

            elif key in [ord('d'), ord('D'), 10, 13]:  # 10, 13 = Enter
                # Proceed with file creation and clipboard if enabled
                return True, self.copy_to_clipboard, False

            elif key == curses.KEY_RESIZE:
                # Handle window resize
                self.update_dimensions()

        return True, self.copy_to_clipboard, False

def interactive_selection(root_node, root_path, output_format='llm', copy_to_clipboard=True):
    """Launch the interactive file selection interface."""
    def _run_interface(stdscr):
        selector = FileSelector(root_node, stdscr, output_format, copy_to_clipboard)
        return selector.run(root_path)
    
    return curses.wrapper(_run_interface)

def write_file_tree_to_string(node, prefix='', is_last=True):
    """Write the file tree as a string."""
    result = ""

    if node.parent is not None:  # Skip root node
        branch = "└── " if is_last else "├── "
        result += f"{prefix}{branch}{node.name}\n"

    if node.is_dir and node.children:
        items = sorted(node.children.items(),
                      key=lambda x: (not x[1].is_dir, x[0].lower()))

        for i, (_, child) in enumerate(items):
            is_last_child = i == len(items) - 1
            new_prefix = prefix + ('    ' if is_last else '│   ')
            result += write_file_tree_to_string(child, new_prefix, is_last_child)

    return result

def generate_output_filename(directory_path, output_format='txt'):
    """Generate a unique output filename based on the directory name."""
    base_name = os.path.basename(os.path.abspath(directory_path))
    extension = f".{output_format}"

    # Start with the base name
    output_name = f"{base_name}{extension}"
    counter = 1

    # If file exists, add a counter
    while os.path.exists(output_name):
        output_name = f"{base_name}({counter}){extension}"
        counter += 1

    return output_name

def write_output_file(output_path, root_path, root_node, file_contents, output_format='txt', dependencies=None):
    """
    Write the file tree and selected content to an output file.

    Formats:
        - txt: Simple text format with <file_map> and <file_contents> sections
        - md: GitHub-compatible markdown
        - llm: Format optimized for LLMs
    """
    if output_format == 'md':
        write_markdown_output(output_path, root_path, root_node, file_contents)
    elif output_format == 'llm':
        if dependencies is None:
            # Collect all files for analysis
            all_files = collect_all_content(root_node, root_path)
            dependencies = analyze_dependencies(root_path, all_files)
        write_llm_optimized_output(output_path, root_path, root_node, file_contents, dependencies)
    else:
        # Default txt format
        with open(output_path, 'w', encoding='utf-8') as f:
            # Write file tree
            f.write("<file_map>\n")
            f.write(f"{root_path}\n")

            tree_str = write_file_tree_to_string(root_node)
            f.write(tree_str)

            f.write("</file_map>\n\n")

            # Write file contents
            f.write("<file_contents>\n")
            for path, content in file_contents:
                f.write(f"File: {path}\n")
                f.write("```")

                # Determine extension for syntax highlighting
                ext = os.path.splitext(path)[1][1:].lower()
                if ext:
                    f.write(ext)

                f.write("\n")
                f.write(content)
                if not content.endswith('\n'):
                    f.write('\n')
                f.write("```\n\n")

            f.write("</file_contents>\n")

    return output_path

def write_markdown_output(output_path, root_path, root_node, file_contents):
    """Write output in GitHub-compatible markdown format."""
    with open(output_path, 'w', encoding='utf-8') as f:
        # Write header
        f.write(f"# Project Files: `{root_path}`\n\n")

        # Write file structure section
        f.write("## 📁 File Structure\n\n")
        f.write("```\n")
        f.write(f"{root_path}\n")
        f.write(write_file_tree_to_string(root_node))
        f.write("```\n\n")

        # Write file contents section
        f.write("## 📄 File Contents\n\n")

        for path, content in file_contents:
            f.write(f"### {path}\n\n")

            # Add syntax highlighting based on extension
            ext = os.path.splitext(path)[1][1:].lower()
            f.write(f"```{ext}\n")
            f.write(content)
            if not content.endswith('\n'):
                f.write('\n')
            f.write("```\n\n")

def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description=f"CodeSelect v{__version__} - Select files to share with AI assistants"
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Directory to scan (default: current directory)"
    )
    parser.add_argument(
        "-o", "--output",
        help="Output file path (default: based on directory name)"
    )
    parser.add_argument(
        "--format",
        choices=["txt", "md", "llm"],
        default="llm",
        help="Output format (default: llm - optimized for LLMs)"
    )
    parser.add_argument(
        "--skip-selection",
        action="store_true",
        help="Skip the selection interface and include all files"
    )
    parser.add_argument(
        "--no-clipboard",
        action="store_true",
        help="Disable automatic copy to clipboard"
    )
    parser.add_argument(
        "--clipboard-only",
        action="store_true",
        help="Copy to clipboard only (don't create a file)"
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Show version information"
    )

    args = parser.parse_args()

    # Handle version display
    if args.version:
        print(f"CodeSelect v{__version__}")
        sys.exit(0)

    # Resolve directory path
    root_path = os.path.abspath(args.directory)
    if not os.path.isdir(root_path):
        print(f"Error: {root_path} is not a valid directory")
        return 1

    # Generate output filename if not specified
    if not args.output and not args.clipboard_only:
        args.output = generate_output_filename(root_path, args.format)

    print(f"Scanning directory: {root_path}")
    root_node = build_file_tree(root_path)

    # Check if clipboard-only was requested from command line
    if args.clipboard_only:
        handle_clipboard_only(root_path, root_node, args.format)
        # Cette fonction ne revient jamais, elle quitte le programme
    
    copy_to_clipboard = not args.no_clipboard
    proceed = True
    
    if not args.skip_selection:
        # Launch interactive selection interface
        try:
            result = interactive_selection(root_node, root_path, args.format, copy_to_clipboard)
            
            # Unpack result tuple
            if isinstance(result, tuple) and len(result) >= 2:
                proceed, copy_setting = result[:2]
                if proceed and copy_setting is not None:
                    copy_to_clipboard = copy_setting
            else:
                proceed = result
                
            if not proceed:
                print("Selection cancelled. Exiting without saving.")
                return 0
                
        except Exception as e:
            print(f"Error in selection interface: {e}")
            return 1

    # Count selected files
    selected_count = count_selected_files(root_node)
    print(f"\nSelected files: {selected_count}")

    # Exit if no files selected
    if selected_count == 0:
        print("No files selected. Exiting.")
        return 0

    # Get the cached content or generate it
    formatted_content, file_contents = generate_formatted_content(root_path, root_node, args.format)
        
    # Write output file
    output_path = write_output_file(args.output, root_path, root_node, file_contents, args.format)
    print(f"\nOutput written to: {output_path}")

    # Copy to clipboard if enabled - using the optimized direct method
    if copy_to_clipboard:
        print("Copying to clipboard...")
        if optimized_copy_to_clipboard(formatted_content):
            print("Content copied to clipboard successfully!")
        else:
            print("Failed to copy to clipboard.")

    return 0

if __name__ == "__main__":
    sys.exit(main())