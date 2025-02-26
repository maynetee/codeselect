# CodeSelect

<div align="center">

![CodeSelect Logo](https://img.shields.io/badge/CodeSelect-1.0.0-blue)

**Easily select and share code with AI assistants**

</div>

CodeSelect is a lightweight tool that helps developers share code with AI assistants like Claude or ChatGPT. It provides a simple interface to select files from a project and exports them in an AI-friendly format with intelligent context about project structure and relationships between files.

## 🚀 Quick Install

```bash
# One-line installation
curl -sSL https://raw.githubusercontent.com/maynetee/codeselect/main/install.sh | bash
```

## ✨ Features

- **Visual File Selection**: Interactive UI to easily select files with checkboxes
- **Intelligent Code Analysis**: Automatically detects imports and relationships between files
- **Multi-language Support**: Works with Python, C/C++, JavaScript, Java, Go, Ruby, PHP, Rust, Swift and more
- **Zero Dependencies**: Works with standard Python libraries only
- **Non-admin Installation**: No special privileges required
- **Clipboard Integration**: Automatically copies output to clipboard
- **AI-optimized Format**: Structures output to help AI assistants understand project context

## 🛠️ Usage

```bash
# Navigate to your project directory
cd ~/projects/myproject

# Run CodeSelect
codeselect

# Export specific directory with custom filename
codeselect /path/to/project -o output.txt

# Help and options
codeselect --help
```

## 🖥️ Interface Controls

- **↑/↓**: Navigate between files
- **Space**: Toggle selection of file/directory
- **←/→**: Collapse/expand directories
- **A**: Select all files
- **N**: Deselect all files
- **C**: Toggle clipboard copy
- **D** or **Enter**: Complete selection and export
- **X** or **Esc**: Exit without saving

## 📄 Output Formats

CodeSelect offers three output formats:

- **LLM** (default): Optimized for language models with context about file relationships
- **Markdown**: GitHub-compatible markdown with syntax highlighting
- **TXT**: Plain text format for maximum compatibility

```bash
# Generate GitHub-compatible markdown
codeselect --format md

# Generate AI-optimized format
codeselect --format llm
```

## ⚙️ Advanced Options

```
usage: codeselect [-h] [-o OUTPUT] [--format {txt,md,llm}] [--skip-selection] [--no-clipboard] [--version] [directory]

CodeSelect v1.0.0 - Select files to share with AI assistants

positional arguments:
  directory             Directory to scan (default: current directory)

options:
  -h, --help            show this help message and exit
  -o OUTPUT, --output OUTPUT
                        Output file path (default: based on directory name)
  --format {txt,md,llm}
                        Output format (default: llm - optimized for LLMs)
  --skip-selection      Skip the selection interface and include all files
  --no-clipboard        Disable automatic copy to clipboard
  --version             Show version information
```

## 🔍 How It Works

1. **Scan**: CodeSelect scans your project directory structure
2. **Select**: Interactive interface lets you choose which files to include
3. **Analyze**: Automatically detects dependencies between files
4. **Generate**: Creates a specially formatted output optimized for AI assistants
5. **Copy**: Automatically copies result to clipboard for easy pasting

## 💻 Development

CodeSelect was developed in just 30 minutes with the help of Claude, Anthropic's AI assistant. It demonstrates how AI can help create useful developer tools through iterative improvements.

Contributions are welcome! Please feel free to submit a Pull Request.
