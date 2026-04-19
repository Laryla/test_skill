#!/usr/bin/env python3
"""
Save content to a txt file with proper encoding and error handling.

Usage:
    python save_content.py --content "Your content here" --filename "output.txt"
    python save_content.py --content "Your content here" --filename "output.txt" --path "/custom/path"
"""

import argparse
import os
import sys
from pathlib import Path


def save_content_to_txt(content: str, filename: str, directory: str = None) -> str:
    """
    Save content to a txt file.
    
    Args:
        content: The text content to save
        filename: The name of the file (with or without .txt extension)
        directory: Optional directory path. If not provided, uses current directory
    
    Returns:
        The full path to the saved file
    
    Raises:
        ValueError: If content is empty or filename is invalid
        IOError: If file cannot be written
    """
    if not content or not content.strip():
        raise ValueError("Content cannot be empty")
    
    if not filename or not filename.strip():
        raise ValueError("Filename cannot be empty")
    
    # Ensure filename has .txt extension
    if not filename.endswith('.txt'):
        filename = f"{filename}.txt"
    
    # Determine the full file path
    if directory:
        dir_path = Path(directory)
        dir_path.mkdir(parents=True, exist_ok=True)
        file_path = dir_path / filename
    else:
        file_path = Path(filename)
    
    # Write content to file with UTF-8 encoding
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return str(file_path.absolute())
    except Exception as e:
        raise IOError(f"Failed to write file: {e}")


def main():
    parser = argparse.ArgumentParser(description='Save content to a txt file')
    parser.add_argument('--content', required=True, help='Content to save')
    parser.add_argument('--filename', required=True, help='Output filename')
    parser.add_argument('--path', help='Optional directory path', default=None)
    
    args = parser.parse_args()
    
    try:
        saved_path = save_content_to_txt(args.content, args.filename, args.path)
        print(f"✅ Content saved successfully to: {saved_path}")
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
