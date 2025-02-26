#!/bin/bash
# Installation script for CodeSelect - no admin privileges required

set -e

echo "Installing CodeSelect..."

# Determine installation directory
USER_BIN="$HOME/.local/bin"
mkdir -p "$USER_BIN"

# Create CodeSelect file
CODESELECT_PATH="$USER_BIN/codeselect"

# Download or create the file
echo "Downloading CodeSelect..."
curl -fsSL https://raw.githubusercontent.com/username/codeselect/main/codeselect.py -o "$CODESELECT_PATH" 2>/dev/null || {
  # If curl fails (e.g., GitHub URL not yet available), copy from the local file
  if [ -f "codeselect.py" ]; then
    cp "codeselect.py" "$CODESELECT_PATH"
  else
    echo "Error: Cannot download or find codeselect.py"
    exit 1
  fi
}

# Make the script executable
chmod +x "$CODESELECT_PATH"

# Check if the directory is in PATH
if [[ ":$PATH:" != *":$USER_BIN:"* ]]; then
    # Determine shell config file
    SHELL_CONFIG=""
    if [[ "$SHELL" == *"zsh"* ]]; then
        SHELL_CONFIG="$HOME/.zshrc"
    elif [[ "$SHELL" == *"bash"* ]]; then
        if [[ "$(uname)" == "Darwin" ]] && [[ ! -f "$HOME/.bashrc" ]]; then
            SHELL_CONFIG="$HOME/.bash_profile"
        else
            SHELL_CONFIG="$HOME/.bashrc"
        fi
    else
        SHELL_CONFIG="$HOME/.profile"
    fi
    
    # Add to PATH
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_CONFIG"
    echo "Added $USER_BIN to your PATH in $SHELL_CONFIG"
    echo "To use immediately, run: source $SHELL_CONFIG"
fi

echo "
Installation complete!

Usage:
  codeselect                 # Analyze current directory
  codeselect /path/to/project  # Analyze a specific directory
  codeselect --help          # Show help

CodeSelect is now installed at: $CODESELECT_PATH
"

# Try to add tab completion for bash
if [[ "$SHELL" == *"bash"* ]]; then
    COMPLETION_FILE="$HOME/.local/share/bash-completion/completions"
    mkdir -p "$COMPLETION_FILE"
    cat > "$COMPLETION_FILE/codeselect" << 'EOF'
_codeselect_completion() {
    local cur prev opts
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    
    # Basic options
    opts="--format --output --skip-selection --no-clipboard --version --help"
    
    # Handle specific options
    case "${prev}" in
        --format|-f)
            COMPREPLY=( $(compgen -W "txt md llm" -- "${cur}") )
            return 0
            ;;
        --output|-o)
            COMPREPLY=( $(compgen -f -- "${cur}") )
            return 0
            ;;
        *)
            ;;
    esac
    
    # Complete options or directories
    if [[ ${cur} == -* ]]; then
        COMPREPLY=( $(compgen -W "${opts}" -- "${cur}") )
    else
        COMPREPLY=( $(compgen -d -- "${cur}") )
    fi
    return 0
}

complete -F _codeselect_completion codeselect
EOF
    echo "Added bash tab completion for CodeSelect"
fi

exit 0
