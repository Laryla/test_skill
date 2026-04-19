---
name: save-as-txt
description: Save text content to txt files with proper encoding and error handling. Use when you need to save content to a .txt file. Supports custom filenames, directory paths, and automatic UTF-8 encoding. Automatically adds .txt extension if not provided.
---

# Save as TXT

Save text content to txt files with proper encoding and error handling.

## Quick Start

Use the `save_content.py` script to save content:

```bash
python scripts/save_content.py --content "Your content here" --filename "output.txt"
```

## Usage

### Basic Usage

Save content to a txt file in the current directory:

```bash
python scripts/save_content.py --content "Your text content" --filename "my_file.txt"
```

### Custom Directory

Save content to a specific directory:

```bash
python scripts/save_content.py --content "Your text content" --filename "my_file.txt" --path "/custom/directory"
```

The script will automatically create the directory if it doesn't exist.

### Automatic Extension

If the filename doesn't have a `.txt` extension, it will be added automatically:

```bash
python scripts/save_content.py --content "Content" --filename "my_file"
# Creates: my_file.txt
```

## Features

- **UTF-8 Encoding**: Ensures proper handling of special characters and multilingual text
- **Auto-extension**: Automatically adds .txt extension if missing
- **Directory Creation**: Creates directories if they don't exist
- **Error Handling**: Validates content and filename, provides clear error messages
- **Path Flexibility**: Works with relative and absolute paths

## Parameters

- `--content` (required): The text content to save
- `--filename` (required): The name of the output file
- `--path` (optional): Directory path for the file (defaults to current directory)

## Examples

```bash
# Save simple text
python scripts/save_content.py --content "Hello World" --filename "hello.txt"

# Save with custom directory
python scripts/save_content.py --content "Important notes" --filename "notes.txt" --path "./documents"

# Save without extension (auto-added)
python scripts/save_content.py --content "Log entry" --filename "log" --path "/var/logs"
```
