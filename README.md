# 進出口廠商資料爬蟲系統

這個專案是一個用於爬取外貿協會進出口廠商資料的自動化系統，能夠獲取公司基本資料和實績級距，並將數據存儲到 PostgreSQL 資料庫中。

## 功能特點

- 自動爬取公司統一編號、基本資料及實績級距
- OCR 自動識別驗證碼
- 資料自動儲存至 PostgreSQL 資料庫
- 自動產生 PDF 報表
- Docker 容器化部署，易於安裝和使用
- 批次處理多家公司資料

## 系統需求

- Docker 和 Docker Compose
- 網際網路連線 (用於網頁訪問及驗證碼辨識)
- 至少 2GB RAM 和 1GB 磁碟空間

## 快速開始

### 1. 複製專案

```bash
git clone https://github.com/your-username/fbfh_scraper.git
cd fbfh_scraper
```

### 2. 建立 Docker 容器

```bash
docker-compose build
```

### 3. 啟動服務

```bash
docker-compose up -d
```

### 4. 執行爬蟲 (處理預設公司列表)

```bash
docker-compose run scraper python scrape_and_print.py --batch
```

### 5. 執行爬蟲 (指定公司統一編號)

```bash
docker-compose run scraper python scrape_and_print.py 22099131 22178368
```

## 命令列參數

```
usage: scrape_and_print.py [-h] [--output OUTPUT] [--no-db] [--batch] [--limit LIMIT] [company_ids ...]

爬取公司基本資料與實績級距

positional arguments:
  company_ids          要爬取的統一編號列表

options:
  -h, --help           show this help message and exit
  --output OUTPUT, -o OUTPUT
                       輸出目錄 (預設: downloads)
  --no-db              不保存到資料庫
  --batch, -b          批次處理預設公司列表
  --limit LIMIT, -l LIMIT
                       限制處理公司數量 (預設處理全部)
```

## 資料庫結構

爬蟲會建立以下資料表：

1. `company_basic`: 存儲公司基本資料
2. `company_grade`: 存儲公司實績級距
3. `scraping_errors`: 記錄爬蟲錯誤

### 資料庫連線設定

資料庫連線參數可透過環境變數設定，預設值為：

- `POSTGRES_HOST`: postgres
- `POSTGRES_PORT`: 5432
- `POSTGRES_DB`: company_data
- `POSTGRES_USER`: postgres
- `POSTGRES_PASSWORD`: 1234

## 專案結構

```
fbfh_scraper/
├── Dockerfile                  # Docker 映像檔設定
├── docker-compose.yml         # Docker Compose 配置
├── requirements.txt           # Python 依賴套件
├── scrape_and_print.py        # 主程式
├── wait-for-postgres.sh       # PostgreSQL 啟動等待腳本
└── downloads/                 # 下載的 PDF 檔案存放目錄
```

## 疑難排解

### 驗證碼識別問題

如果遇到驗證碼識別率低的問題，可以嘗試：

1. 確保容器內的 Chrome 和 ChromeDriver 版本匹配
2. 檢查 ddddocr 套件是否正確安裝
3. 增加 `max_attempts` 參數值讓系統有更多重試機會


## 環境變數

| 變數名稱 | 說明 | 預設值 |
|---------|------|-------|
| POSTGRES_HOST | PostgreSQL 主機名 | postgres |
| POSTGRES_PORT | PostgreSQL 埠號 | 5432 |
| POSTGRES_DB | PostgreSQL 資料庫名稱 | company_data |
| POSTGRES_USER | PostgreSQL 使用者名稱 | postgres |
| POSTGRES_PASSWORD | PostgreSQL 密碼 | 1234 |
| DISPLAY | Xvfb 顯示設定 | :99 |

## 開發指南

### 本地開發環境設置

1. 啟動服務但保持容器運行：

```bash
docker-compose run --entrypoint bash -d scraper
```

2. 進入容器執行開發：

```bash
docker exec -it scraper bash
```

3. 修改代碼後在容器內執行：

```bash
python scrape_and_print.py --limit 1
```

### 功能模組說明

本專案主要包含以下功能模組：

1. **驗證碼識別模組**：使用 ddddocr 進行驗證碼辨識
2. **網頁爬蟲模組**：使用 Selenium 和 Chrome WebDriver 進行網頁自動化
3. **資料擷取模組**：從網頁中擷取所需資料
4. **資料庫模組**：將擷取的資料存入 PostgreSQL 資料庫
5. **PDF 生成模組**：將擷取的資料生成為 PDF 報表

每個模組都有明確的函數定義，可以根據需求進行修改或擴展。

### 擴展功能

若要添加新功能，通常需要修改以下文件：

1. `scrape_and_print.py`：主程式邏輯
2. `requirements.txt`：如有新增依賴套件
3. `Dockerfile`：如有新的系統依賴
4. `docker-compose.yml`：如有新的服務或配置

例如，如果要增加爬取其他類型的資料，可以在 `extract_company_data` 函數中添加新的爬取邏輯，並在資料庫模組中添加對應的資料表和存儲邏輯。

## 注意事項

- 本工具僅供學習研究使用，請勿用於商業用途
- 請遵守爬蟲使用網站的相關規範
- 爬取資料時請添加適當延遲，避免對目標網站造成負擔
- 請妥善保管獲取的公司資料，遵守相關隱私法規
