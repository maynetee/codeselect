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
from pathlib import Path
import datetime

__version__ = "1.0.0"

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
        ignore_patterns = ['.git', '__pycache__', '*.pyc', '.DS_Store', '.idea', '.vscode']
    
    def should_ignore(path):
        for pattern in ignore_patterns:
            if fnmatch.fnmatch(os.path.basename(path), pattern):
                return True
        return False
    
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
    for dirpath, dirnames, filenames in os.walk(root_path):
        # Skip filtered directories
        dirnames[:] = [d for d in dirnames if not should_ignore(os.path.join(dirpath, d))]
            
        rel_path = os.path.relpath(dirpath, root_path)
        if rel_path == '.':
            # Add files in root
            for filename in filenames:
                if filename not in root_node.children and not should_ignore(filename):
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
                    if not should_ignore(filename) and filename not in current.children:
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
        full_path = os.path.join(root_path, file_path)
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()
            results.append((file_path, content))
        except UnicodeDecodeError:
            print(f"Ignoring binary file: {file_path}")
        except Exception as e:
            print(f"Error reading {file_path}: {e}")
    elif node.is_dir and node.children:
        for child in node.children.values():
            results.extend(collect_selected_content(child, root_path))
    
    return results

def collect_all_content(node, root_path):
    """Collect content from all files (for analysis)."""
    results = []
    
    if not node.is_dir:
        file_path = node.path
        full_path = os.path.join(root_path, file_path)
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
    """Analyze relationships between project files.
    
    Detects dependencies for multiple programming languages
    by analyzing imports, includes, references, etc.
    """
    dependencies = {}
    imports = {}
    
    # Define detection patterns for different languages
    language_patterns = {
        # Python
        '.py': [
            r'^from\s+([\w.]+)\s+import', 
            r'^import\s+([\w.]+)',
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
        ],
        '.ts': [
            r'(?:import|require)\s*\(?[\'"]([@\w\-./]+)[\'"]',
            r'from\s+[\'"]([@\w\-./]+)[\'"]',
        ],
        # Java
        '.java': [
            r'import\s+([\w.]+)',
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
        ],
        # PHP
        '.php': [
            r'(?:require|include)(?:_once)?\s*\(?[\'"]([^\'"]+)[\'"]',
            r'use\s+([\w\\]+)',
        ],
        # Rust
        '.rs': [
            r'use\s+([\w:]+)',
            r'extern\s+crate\s+([\w]+)',
        ],
        # Swift
        '.swift': [
            r'import\s+(\w+)',
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
            ]
            
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
                dir_files = [p for p, _ in file_contents if p.startswith(f"{dir_node.name}/")]
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
        
        # Find most referenced files
        referenced_by = {}
        for file, deps in dependencies.items():
            for dep in deps:
                if isinstance(dep, str) and os.path.sep in dep:  # It's a file path
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
                internal_deps = [d for d in deps if isinstance(d, str) and os.path.sep in d]
                external_deps = [d for d in deps if d not in internal_deps]
                
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
                internal_deps = [d for d in file_deps if isinstance(d, str) and os.path.sep in d]
                external_deps = [d for d in file_deps if d not in internal_deps]
                
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
        'ts': 'TypeScript',
        'java': 'Java',
        'html': 'HTML',
        'css': 'CSS',
        'php': 'PHP',
        'rb': 'Ruby',
        'go': 'Go',
        'rs': 'Rust',
        'swift': 'Swift',
        'kt': 'Kotlin',
        'sh': 'Shell',
        'md': 'Markdown',
        'json': 'JSON',
        'xml': 'XML',
        'yaml': 'YAML',
        'yml': 'YAML',
        'sql': 'SQL',
        'r': 'R',
    }
    return language_map.get(extension, extension.upper())

def try_copy_to_clipboard(text):
    """Attempt to copy text to clipboard with graceful fallback."""
    try:
        # Try different clipboard methods
        try:
            import pyperclip
            pyperclip.copy(text)
            return True
        except (ImportError, ModuleNotFoundError):
            # Try platform-specific methods
            if sys.platform == 'darwin':  # macOS
                process = subprocess.Popen(['pbcopy'], stdin=subprocess.PIPE)
                process.communicate(text.encode('utf-8'))
                return True
            elif sys.platform == 'win32':  # Windows
                process = subprocess.Popen(['clip'], stdin=subprocess.PIPE)
                process.communicate(text.encode('utf-8'))
                return True
            elif sys.platform.startswith('linux'):  # Linux
                try:
                    process = subprocess.Popen(['xclip', '-selection', 'clipboard'], stdin=subprocess.PIPE)
                    process.communicate(text.encode('utf-8'))
                    return True
                except FileNotFoundError:
                    try:
                        process = subprocess.Popen(['xsel', '-ib'], stdin=subprocess.PIPE)
                        process.communicate(text.encode('utf-8'))
                        return True
                    except FileNotFoundError:
                        return False
            return False
    except:
        return False

class FileSelector:
    def __init__(self, root_node, stdscr):
        self.root_node = root_node
        self.stdscr = stdscr
        self.current_index = 0
        self.scroll_offset = 0
        self.visible_nodes = flatten_tree(root_node)
        self.max_visible = 0
        self.height, self.width = 0, 0
        self.copy_to_clipboard = True  # Default: copy to clipboard enabled
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
        self.max_visible = self.height - 5  # Keep space for help text
    
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
        
        # Draw visible nodes
        for i, (node, level) in enumerate(self.visible_nodes[self.scroll_offset:self.scroll_offset + self.max_visible]):
            y = i
            if y >= self.max_visible:
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
        help_y = self.height - 4
        self.stdscr.addstr(help_y, 0, "━" * self.width)
        help_y += 1
        self.stdscr.addstr(help_y, 0, "↑/↓: Navigate  SPACE: Select  ←/→: Close/Open folder", curses.color_pair(6))
        help_y += 1
        clip_status = "ON" if self.copy_to_clipboard else "OFF"
        self.stdscr.addstr(help_y, 0, f"A: Select All  N: Select None  C: Clipboard ({clip_status})  X: Exit  D: Done", curses.color_pair(6))
        
        # Display statistics
        selected_count = count_selected_files(self.root_node)
        total_count = sum(1 for node, _ in self.visible_nodes if not node.is_dir)
        self.stdscr.addstr(0, 0, f"Selected files: {selected_count}/{total_count}", curses.A_BOLD)
        
        self.stdscr.refresh()
    
    def toggle_selection(self, node):
        """Toggle selection of a node and its children if it's a directory."""
        node.selected = not node.selected
        
        if node.is_dir and node.children:
            for child in node.children.values():
                child.selected = node.selected
                if child.is_dir:
                    self.toggle_selection(child)
    
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
    
    def run(self):
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
                
            elif key in [ord('c'), ord('C')]:
                # Toggle clipboard
                self.copy_to_clipboard = not self.copy_to_clipboard
            
            elif key in [ord('x'), ord('X'), 27]:  # 27 = ESC
                # Exit without saving
                return False
            
            elif key in [ord('d'), ord('D'), 10, 13]:  # 10, 13 = Enter
                # Done
                return True
            
            elif key == curses.KEY_RESIZE:
                # Handle window resize
                self.update_dimensions()
        
        return True

def interactive_selection(root_node):
    """Launch the interactive file selection interface."""
    return curses.wrapper(lambda stdscr: FileSelector(root_node, stdscr).run())

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
    if not args.output:
        args.output = generate_output_filename(root_path, args.format)
    
    print(f"Scanning directory: {root_path}")
    root_node = build_file_tree(root_path)
    
    proceed = True
    if not args.skip_selection:
        # Launch interactive selection interface
        try:
            proceed = interactive_selection(root_node)
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
    
    # Collect content from selected files
    file_contents = collect_selected_content(root_node, root_path)
    print(f"Collected content from {len(file_contents)} files.")
    
    # Analyze dependencies if using LLM format
    if args.format == 'llm':
        print("Analyzing file relationships...")
        all_files = collect_all_content(root_node, root_path)
        dependencies = analyze_dependencies(root_path, all_files)
        
        # Write output with dependencies
        output_path = write_output_file(args.output, root_path, root_node, file_contents, args.format, dependencies)
    else:
        # Write output without dependencies
        output_path = write_output_file(args.output, root_path, root_node, file_contents, args.format)
    
    print(f"\nOutput written to: {output_path}")
    
    # Copy to clipboard if enabled
    if not args.no_clipboard:
        try:
            with open(output_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            if try_copy_to_clipboard(content):
                print("Content copied to clipboard.")
            else:
                print("Could not copy to clipboard (missing dependencies).")
        except Exception as e:
            print(f"Error copying to clipboard: {e}")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
