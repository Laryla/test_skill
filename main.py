#!/usr/bin/env python3
"""
文件转 Markdown API 服务

支持文件上传并转换为 Markdown
"""

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import uuid
import shutil

try:
    from markitdown import MarkItDown
except ImportError:
    raise ImportError("请先安装: uv add 'markitdown[all]'")

app = FastAPI(title="文件转 Markdown 服务")

# 初始化 MarkItDown
md = MarkItDown()

# 创建必要的目录
UPLOAD_DIR = Path(".deer-flow/threads/example-thread/user-data/uploads")
OUTPUT_DIR = Path(".deer-flow/threads/example-thread/user-data/uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

@app.get("/", response_class=HTMLResponse)
async def upload_form():
    """文件上传表单页面"""
    return """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>文件转 Markdown</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .container {
            background: white;
            border-radius: 16px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            padding: 40px;
            max-width: 500px;
            width: 100%;
        }
        h1 {
            color: #333;
            margin-bottom: 10px;
            font-size: 28px;
        }
        .subtitle {
            color: #666;
            margin-bottom: 30px;
            font-size: 14px;
        }
        .form-group {
            margin-bottom: 25px;
        }
        label {
            display: block;
            color: #444;
            font-weight: 500;
            margin-bottom: 8px;
            font-size: 14px;
        }
        .file-drop-area {
            border: 2px dashed #667eea;
            border-radius: 8px;
            padding: 40px 20px;
            text-align: center;
            cursor: pointer;
            transition: all 0.3s;
            background: #f8f9ff;
        }
        .file-drop-area:hover {
            border-color: #764ba2;
            background: #f0f2ff;
        }
        .file-drop-area.dragover {
            border-color: #764ba2;
            background: #e8ebff;
        }
        .file-drop-area input[type="file"] {
            display: none;
        }
        .file-icon {
            font-size: 48px;
            margin-bottom: 10px;
        }
        .file-text {
            color: #666;
            font-size: 14px;
        }
        .file-info {
            margin-top: 15px;
            padding: 10px;
            background: #e8ebff;
            border-radius: 6px;
            display: none;
        }
        .file-info.show {
            display: block;
        }
        .supported-formats {
            background: #f5f5f5;
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 25px;
        }
        .supported-formats h3 {
            font-size: 12px;
            color: #666;
            margin-bottom: 8px;
            text-transform: uppercase;
        }
        .formats {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
        }
        .format-tag {
            background: white;
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 11px;
            color: #667eea;
            font-weight: 500;
        }
        button {
            width: 100%;
            padding: 14px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s;
        }
        button:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 20px rgba(102, 126, 234, 0.4);
        }
        button:disabled {
            background: #ccc;
            cursor: not-allowed;
            transform: none;
        }
        .result {
            margin-top: 25px;
            padding: 20px;
            background: #f0f9ff;
            border-radius: 8px;
            display: none;
        }
        .result.show {
            display: block;
        }
        .result h3 {
            color: #0891b2;
            margin-bottom: 10px;
        }
        .download-btn {
            display: inline-block;
            padding: 10px 20px;
            background: #0891b2;
            color: white;
            text-decoration: none;
            border-radius: 6px;
            font-size: 14px;
        }
        .error {
            background: #fef2f2;
            color: #dc2626;
            padding: 15px;
            border-radius: 8px;
            margin-top: 20px;
            display: none;
        }
        .error.show {
            display: block;
        }
        #loading {
            display: none;
            text-align: center;
            margin-top: 20px;
        }
        #loading.show {
            display: block;
        }
        .spinner {
            border: 3px solid #f3f3f3;
            border-top: 3px solid #667eea;
            border-radius: 50%;
            width: 40px;
            height: 40px;
            animation: spin 1s linear infinite;
            margin: 0 auto;
        }
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>📄 文件转 Markdown</h1>
        <p class="subtitle">将各种文件格式快速转换为 Markdown</p>

        <div class="supported-formats">
            <h3>支持的文件格式</h3>
            <div class="formats">
                <span class="format-tag">PDF</span>
                <span class="format-tag">DOCX</span>
                <span class="format-tag">PPTX</span>
                <span class="format-tag">XLSX</span>
                <span class="format-tag">HTML</span>
                <span class="format-tag">PNG/JPG</span>
                <span class="format-tag">MP3/WAV</span>
                <span class="format-tag">TXT</span>
            </div>
        </div>

        <form id="uploadForm">
            <div class="form-group">
                <label>选择文件</label>
                <div class="file-drop-area" id="dropArea">
                    <div class="file-icon">📁</div>
                    <div class="file-text">点击选择文件或拖拽文件到此处</div>
                    <input type="file" id="fileInput" name="file">
                </div>
                <div class="file-info" id="fileInfo"></div>
            </div>
            <button type="submit" id="submitBtn" disabled>转换为 Markdown</button>
        </form>

        <div id="loading">
            <div class="spinner"></div>
            <p style="margin-top: 10px; color: #666;">正在转换中...</p>
        </div>

        <div class="result" id="result"></div>
        <div class="error" id="error"></div>
    </div>

    <script>
        const dropArea = document.getElementById('dropArea');
        const fileInput = document.getElementById('fileInput');
        const fileInfo = document.getElementById('fileInfo');
        const uploadForm = document.getElementById('uploadForm');
        const submitBtn = document.getElementById('submitBtn');
        const resultDiv = document.getElementById('result');
        const errorDiv = document.getElementById('error');
        const loadingDiv = document.getElementById('loading');

        // 点击上传区域
        dropArea.addEventListener('click', () => fileInput.click());

        // 文件选择
        fileInput.addEventListener('change', handleFileSelect);

        // 拖拽上传
        dropArea.addEventListener('dragover', (e) => {
            e.preventDefault();
            dropArea.classList.add('dragover');
        });

        dropArea.addEventListener('dragleave', () => {
            dropArea.classList.remove('dragover');
        });

        dropArea.addEventListener('drop', (e) => {
            e.preventDefault();
            dropArea.classList.remove('dragover');
            const files = e.dataTransfer.files;
            if (files.length > 0) {
                fileInput.files = files;
                handleFileSelect();
            }
        });

        function handleFileSelect() {
            const file = fileInput.files[0];
            if (file) {
                fileInfo.textContent = `已选择: ${file.name} (${formatFileSize(file.size)})`;
                fileInfo.classList.add('show');
                submitBtn.disabled = false;
            }
        }

        function formatFileSize(bytes) {
            if (bytes < 1024) return bytes + ' B';
            if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
            return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
        }

        // 表单提交
        uploadForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const file = fileInput.files[0];
            if (!file) return;

            const formData = new FormData();
            formData.append('file', file);

            submitBtn.disabled = true;
            loadingDiv.classList.add('show');
            resultDiv.classList.remove('show');
            errorDiv.classList.remove('show');

            try {
                const response = await fetch('/convert', {
                    method: 'POST',
                    body: formData
                });

                const data = await response.json();

                if (response.ok) {
                    resultDiv.innerHTML = `
                        <h3>✅ 转换成功!</h3>
                        <p style="margin: 10px 0;">文件已转换为 Markdown</p>
                        <a href="${data.download_url}" class="download-btn" download>⬇️ 下载 Markdown 文件</a>
                    `;
                    resultDiv.classList.add('show');
                } else {
                    throw new Error(data.detail || '转换失败');
                }
            } catch (error) {
                errorDiv.textContent = '❌ ' + error.message;
                errorDiv.classList.add('show');
            } finally {
                loadingDiv.classList.remove('show');
                submitBtn.disabled = false;
            }
        });
    </script>
</body>
</html>
    """


@app.post("/convert")
async def convert_to_markdown(file: UploadFile = File(...)):
    """转换文件为 Markdown"""
    # 生成唯一 ID
    file_id = str(uuid.uuid4())

    # 保存上传的文件
    file_extension = Path(file.filename).suffix
    upload_path = UPLOAD_DIR / f"{file_id}{file_extension}"

    try:
        # 保存上传文件（保留原文件）
        with upload_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # 转换为 Markdown
        result = md.convert(str(upload_path))

        # 保存 Markdown 文件到同一目录
        output_filename = f"{file_id}.md"
        output_path = UPLOAD_DIR / output_filename
        output_path.write_text(result.text_content)

        return {
            "status": "success",
            "filename": file.filename,
            "file_id": file_id,
            "original_path": str(upload_path),
            "output_path": str(output_path),
            "content_length": len(result.text_content)
        }

    except Exception as e:
        # 清理文件
        if upload_path.exists():
            upload_path.unlink()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
