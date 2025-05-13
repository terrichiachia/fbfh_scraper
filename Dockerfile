# === 第一階段：建立 Python 套件 wheel ===
FROM python:3.11-slim AS builder
WORKDIR /app

# 設定 pip
RUN mkdir -p /root/.pip && \
    echo "[global]" > /root/.pip/pip.conf && \
    echo "trusted-host = pypi.org files.pythonhosted.org" >> /root/.pip/pip.conf && \
    echo "timeout = 1000" >> /root/.pip/pip.conf

# 安裝用於下載及解壓的工具
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    wget gnupg unzip curl && \
    rm -rf /var/lib/apt/lists/*

# 複製並打包 Python 依賴
COPY requirements.txt .
RUN pip --trusted-host pypi.org --trusted-host files.pythonhosted.org --default-timeout=1000 install --upgrade pip && \
    pip --trusted-host pypi.org --trusted-host files.pythonhosted.org --default-timeout=1000 wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# === 第二階段：最終運行環境 ===
FROM python:3.11-bullseye
WORKDIR /app

# 設定 pip
RUN mkdir -p /root/.pip && \
    echo "[global]" > /root/.pip/pip.conf && \
    echo "trusted-host = pypi.org files.pythonhosted.org" >> /root/.pip/pip.conf && \
    echo "timeout = 1000" >> /root/.pip/pip.conf

# 安裝基本工具和必要庫，包含 Xvfb 和中文字型
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    wget gnupg unzip curl ca-certificates sudo \
    fonts-noto-cjk fonts-noto-cjk-extra \
    fonts-arphic-ukai fonts-arphic-uming \
    fonts-ipafont-mincho fonts-ipafont-gothic fonts-unfonts-core \
    xvfb x11vnc fluxbox xterm \
    libnss3 libgconf-2-4 libfontconfig1 libxi6 libxshmfence1 \
    libxtst6 fonts-liberation libasound2 libatk-bridge2.0-0 \
    libatk1.0-0 libgdk-pixbuf2.0-0 libgtk-3-0 libgbm1 \
    # OCR 相關依賴
    tesseract-ocr \
    libtesseract-dev \
    tesseract-ocr-eng \
    tesseract-ocr-chi-tra \
    tesseract-ocr-chi-sim \
    # PIL/Pillow 相關依賴
    libjpeg-dev \
    libpng-dev \
    libtiff-dev \
    libfreetype6-dev \
    # OpenCV 相關依賴
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgl1-mesa-glx && \
    rm -rf /var/lib/apt/lists/*

# 設定 Chrome 源並安裝
RUN wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome-keyring.gpg && \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome-keyring.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list && \
    apt-get update && \
    apt-get install -y google-chrome-stable && \
    rm -rf /var/lib/apt/lists/*

# 檢查安裝的 Chrome 版本
RUN CHROME_VERSION=$(google-chrome --version | awk '{print $3}') && \
    echo "Installed Chrome version: $CHROME_VERSION"

# 自動下載對應版本的 ChromeDriver
RUN CHROME_VERSION=$(google-chrome --version | awk '{print $3}') && \
    CHROME_MAJOR=$(echo $CHROME_VERSION | cut -d. -f1) && \
    echo "Detected Chrome Major Version: $CHROME_MAJOR" && \
    LATEST=$(curl -s "https://googlechromelabs.github.io/chrome-for-testing/LATEST_RELEASE_${CHROME_MAJOR}") && \
    curl -sSL "https://storage.googleapis.com/chrome-for-testing-public/${LATEST}/linux64/chromedriver-linux64.zip" -o /tmp/chromedriver.zip && \
    unzip /tmp/chromedriver.zip -d /tmp/ && \
    mv /tmp/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver && \
    chmod +x /usr/local/bin/chromedriver && \
    rm -rf /tmp/chromedriver* /tmp/chromedriver-linux64 && \
    chromedriver --version || echo "ChromeDriver installation failed, but continuing"

# 創建啟動腳本，用於啟動 Xvfb 和 Chrome 的虛擬顯示服務器
RUN echo '#!/bin/bash' > /usr/local/bin/start-xvfb.sh && \
    echo 'Xvfb :99 -screen 0 1920x1080x24 &' >> /usr/local/bin/start-xvfb.sh && \
    echo 'export DISPLAY=:99' >> /usr/local/bin/start-xvfb.sh && \
    echo 'exec "$@"' >> /usr/local/bin/start-xvfb.sh && \
    chmod +x /usr/local/bin/start-xvfb.sh

# 複製並安裝 Python 套件
COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip --trusted-host pypi.org --trusted-host files.pythonhosted.org --default-timeout=1000 install --no-index --no-cache-dir --find-links /wheels -r requirements.txt && \
    rm -rf /wheels

# 複製專案檔案（除了 wait-for-postgres.sh）
COPY . /app/

# 刪除可能存在的 Windows 格式的 wait-for-postgres.sh
RUN rm -f /app/wait-for-postgres.sh

# 創建 wait-for-postgres.sh 腳本（確保在 COPY 之後創建）
RUN echo '#!/bin/bash' > /app/wait-for-postgres.sh && \
    echo 'set -e' >> /app/wait-for-postgres.sh && \
    echo 'host="$1"' >> /app/wait-for-postgres.sh && \
    echo 'shift' >> /app/wait-for-postgres.sh && \
    echo 'cmd="$@"' >> /app/wait-for-postgres.sh && \
    echo 'until PGPASSWORD=$POSTGRES_PASSWORD psql -h "${host%%:*}" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c '"'"'\q'"'"'; do' >> /app/wait-for-postgres.sh && \
    echo '  >&2 echo "Postgres is unavailable - sleeping"' >> /app/wait-for-postgres.sh && \
    echo '  sleep 1' >> /app/wait-for-postgres.sh && \
    echo 'done' >> /app/wait-for-postgres.sh && \
    echo '>&2 echo "Postgres is up - executing command"' >> /app/wait-for-postgres.sh && \
    echo 'exec $cmd' >> /app/wait-for-postgres.sh && \
    chmod +x /app/wait-for-postgres.sh

# 設置 sudo 權限
RUN echo "ALL ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/nopasswd && \
    chmod 0440 /etc/sudoers.d/nopasswd

# 建立下載資料夾並設定權限
RUN mkdir -p /app/downloads && chmod 777 /app/downloads
VOLUME [ "/app/downloads" ]

# 環境變數
ENV PYTHONUNBUFFERED=1
ENV DISPLAY=:99
# 設定 Tesseract 資料路徑環境變數
ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/4.00/tessdata

# 安裝 psql 客戶端工具
RUN apt-get update && \
    apt-get install -y postgresql-client && \
    rm -rf /var/lib/apt/lists/*

# 預設使用啟動腳本啟動 Xvfb 和程式
ENTRYPOINT ["/usr/local/bin/start-xvfb.sh", "/app/wait-for-postgres.sh", "postgres:5432", "--", "python", "scrape_and_print.py"]

# 預設執行指令 - 可選使用 Jupyter Lab
CMD ["jupyter", "lab", "--ip=0.0.0.0", "--allow-root", "--no-browser", "--NotebookApp.token=''", "--NotebookApp.password=''"]