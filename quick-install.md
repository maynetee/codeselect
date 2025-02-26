# CodeSelect - Quick Installation Guide

CodeSelect helps you easily share code with AI assistants like Claude or ChatGPT.

## One-Line Installation (macOS/Linux)

```bash
curl -sSL https://raw.githubusercontent.com/username/codeselect/main/install.sh | bash
```

After installation, you may need to restart your terminal or run:

```bash
source ~/.bashrc  # or ~/.zshrc if you use zsh
```

## Manual Installation (if one-line doesn't work)

1. Download the script:
```bash
mkdir -p ~/.local/bin
curl -sSL https://raw.githubusercontent.com/username/codeselect/main/codeselect.py -o ~/.local/bin/codeselect
chmod +x ~/.local/bin/codeselect
```

2. Add to PATH (if not already):
```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc  # or ~/.zshrc
source ~/.bashrc  # or ~/.zshrc
```

## Basic Usage

```bash
# Navigate to your project directory
cd ~/your-project

# Run CodeSelect
codeselect

# Export with custom filename
codeselect -o output.txt

# See all options
codeselect --help
```

## Key Controls

- **Space**: Select/deselect files
- **A**: Select all
- **N**: Select none
- **X/Esc**: Quit without saving
- **D/Enter**: Complete and save
