# 必须使用微软官方提供的带有完整 Chromium 内核的基础镜像
FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

# 设置工作目录
WORKDIR /app

# 复制依赖列表并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制主程序代码 (确保你上一步的 app.py 也在同级目录)
COPY app.py .

# 暴露 8080 端口供外部访问 (Sealos / Zeabur 识别)
EXPOSE 8080

# 启动程序
CMD ["python", "app.py"]
