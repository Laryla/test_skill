#!/usr/bin/env python3
"""
MarkItDown 测试脚本

测试 markitdown 库将各种文件格式转换为 Markdown 的功能。
支持格式: PDF, DOCX, PPTX, 图片 (PNG/JPG), HTML, 音频等

官方文档: https://github.com/microsoft/markitdown
"""

import sys
from pathlib import Path

try:
    from markitdown import MarkItDown
    md = MarkItDown()
except ImportError:
    print("❌ markitdown 未安装")
    print("请运行: uv add 'markitdown[all]'")
    sys.exit(1)


def test_text_conversion():
    """测试纯文本文件转换"""
    print("\n📝 测试纯文本转换...")

    # 创建测试文本文件
    test_file = Path(__file__).parent / "test_files" / "sample_text.txt"
    test_file.parent.mkdir(exist_ok=True)

    test_file.write_text("# Hello World\n\nThis is a test.")

    result = md.convert(str(test_file))

    # 保存为同名 .md 文件
    output_file = str(test_file) + ".md"
    Path(output_file).write_text(result.text_content)
    print(f"💾 已保存到: {output_file}")

    print(f"\n📄 转换结果:")
    print(result.text_content)
    print("-" * 40)

    assert "# Hello World" in result.text_content
    assert "test" in result.text_content
    print("✅ 纯文本转换通过")


def test_html_conversion():
    """测试 HTML 文件转 Markdown"""
    print("\n🌐 测试 HTML 转 Markdown...")

    # 创建测试 HTML 文件
    test_file = Path(__file__).parent / "test_files" / "sample.html"
    test_file.parent.mkdir(exist_ok=True)

    html_content = """<html>
<body>
    <h1>Title</h1>
    <p>This is a <strong>paragraph</strong>.</p>
    <ul>
        <li>Item 1</li>
        <li>Item 2</li>
    </ul>
</body>
</html>"""

    test_file.write_text(html_content)

    result = md.convert(str(test_file))

    # 保存为同名 .md 文件
    output_file = str(test_file) + ".md"
    Path(output_file).write_text(result.text_content)
    print(f"💾 已保存到: {output_file}")

    assert "Title" in result.text_content
    assert "paragraph" in result.text_content
    print("✅ HTML 转换通过")


def test_code_conversion():
    """测试代码文件转换"""
    print("\n💻 测试代码文件转换...")

    # 创建测试代码文件
    test_file = Path(__file__).parent / "test_files" / "sample.py"
    test_file.parent.mkdir(exist_ok=True)

    code = '''def hello():
    """Say hello"""
    print("Hello, World!")
    return 42
'''

    test_file.write_text(code)

    result = md.convert(str(test_file))

    # 保存为同名 .md 文件
    output_file = str(test_file) + ".md"
    Path(output_file).write_text(result.text_content)
    print(f"💾 已保存到: {output_file}")

    assert "def hello" in result.text_content or "hello" in result.text_content
    print("✅ 代码文件转换通过")


def test_image_conversion():
    """测试图片转 Markdown (需要图片文件)"""
    print("\n🖼️  测试图片转 Markdown...")

    # 创建测试图片目录
    test_dir = Path(__file__).parent / "test_files"
    test_dir.mkdir(exist_ok=True)

    # 查找测试图片
    image_extensions = [".png", ".jpg", ".jpeg", ".gif", ".webp"]
    test_images = [
        f for f in test_dir.iterdir()
        if f.suffix.lower() in image_extensions
    ]

    if not test_images:
        print("⚠️  未找到测试图片，跳过")
        return

    for img_path in test_images[:1]:  # 只测试第一个
        result = md.convert(str(img_path))

        # 保存为同名 .md 文件
        output_file = str(img_path) + ".md"
        Path(output_file).write_text(result.text_content)
        print(f"  📷 {img_path.name}: {len(result.text_content)} 字符")
        print(f"  💾 已保存到: {output_file}")
        print("✅ 图片转换通过")


def test_file_path():
    """测试从文件路径读取"""
    print("\n📁 测试文件路径读取...")

    # 创建测试文件
    test_file = Path(__file__).parent / "test_files" / "sample.txt"
    test_file.parent.mkdir(exist_ok=True)

    test_file.write_text("# Test File\n\nThis is sample content.\n\n- Item A\n- Item B")

    result = md.convert(str(test_file))

    # 保存为同名 .md 文件
    output_file = str(test_file) + ".md"
    Path(output_file).write_text(result.text_content)
    print(f"💾 已保存到: {output_file}")

    assert "Test File" in result.text_content
    assert "sample content" in result.text_content
    print("✅ 文件路径读取通过")


def main():
    """运行所有测试"""
    print("=" * 50)
    print("🧪 MarkItDown 测试套件")
    print("=" * 50)

    tests = [
        test_text_conversion,
        test_html_conversion,
        test_code_conversion,
        test_image_conversion,
        test_file_path,
    ]

    passed = 0
    failed = 0

    for test_func in tests:
        try:
            test_func()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"❌ {test_func.__name__} 失败: {e}")

    print("\n" + "=" * 50)
    print(f"📊 结果: {passed} 通过, {failed} 失败")
    print("=" * 50)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
