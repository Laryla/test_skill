#!/usr/bin/env python3
"""
Extracts textual content from a supplied document file (txt, pdf, docx).
Detects filetype, uses appropriate extractor, and outputs plain text to stdout.
"""
import sys
import os

def extract_txt(file_path):
    with open(file_path, encoding='utf-8') as f:
        return f.read()

def extract_pdf(file_path):
    try:
        import PyPDF2
    except ImportError:
        print('PyPDF2 required for PDF extraction', file=sys.stderr)
        sys.exit(1)
    result = []
    with open(file_path, 'rb') as f:
        pdf = PyPDF2.PdfReader(f)
        for page in pdf.pages:
            result.append(page.extract_text())
    return '\n'.join(result)

def extract_docx(file_path):
    try:
        import docx
    except ImportError:
        print('python-docx required for DOCX extraction', file=sys.stderr)
        sys.exit(1)
    doc = docx.Document(file_path)
    return '\n'.join([para.text for para in doc.paragraphs])

def main():
    if len(sys.argv) != 2:
        print(f"Usage: {os.path.basename(sys.argv[0])} <input_file>")
        sys.exit(1)
    file_path = sys.argv[1]
    ext = os.path.splitext(file_path)[1].lower()
    if ext == '.txt':
        print(extract_txt(file_path))
    elif ext == '.pdf':
        print(extract_pdf(file_path))
    elif ext == '.docx':
        print(extract_docx(file_path))
    else:
        print(f"Unsupported file type: {ext}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
