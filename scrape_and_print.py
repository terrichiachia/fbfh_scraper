#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import io
import base64
import logging
import re
import sys
from datetime import datetime
from PIL import Image, ImageEnhance
import ddddocr
import psycopg2
import random
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    WebDriverException,
)

# 設置日誌
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


# PostgreSQL 連接設定
PG_CONFIG = {
    "host": os.environ.get("POSTGRES_HOST", "postgres"),
    "port": int(os.environ.get("POSTGRES_PORT", "5432")),
    "database": os.environ.get("POSTGRES_DB", "company_data"),
    "user": os.environ.get("POSTGRES_USER", "postgres"),
    "password": os.environ.get("POSTGRES_PASSWORD", "1234"),
}


def connect_to_postgres():
    """連接 PostgreSQL 資料庫，如果失敗則返回 None"""
    try:
        conn = psycopg2.connect(**PG_CONFIG)
        logging.info("已成功連接到 PostgreSQL 資料庫")
        return conn
    except Exception as e:
        logging.error(f"連接到 PostgreSQL 時出錯：{e}")
        return None


def create_tables(conn):
    """確保必要的資料表存在"""
    if not conn:
        logging.warning("無法建立資料表：資料庫連接失敗")
        return False

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
            CREATE TABLE IF NOT EXISTS company_basic (
                company_id VARCHAR(10) PRIMARY KEY,
                issue_date VARCHAR(20), reg_date VARCHAR(20),
                cn_name TEXT, en_name TEXT, cn_address TEXT, en_address TEXT,
                representative TEXT, tel1 VARCHAR(20), tel2 VARCHAR(20), fax VARCHAR(20),
                old_cn_name TEXT, old_en_name TEXT, website TEXT, email TEXT,
                import_qualification VARCHAR(10), export_qualification VARCHAR(10),
                import_items_cn TEXT, import_items_en TEXT,
                export_items_cn TEXT, export_items_en TEXT,
                fetch_date TIMESTAMP,
                status VARCHAR(20) DEFAULT 'success'
            )"""
            )
            cur.execute(
                """
            CREATE TABLE IF NOT EXISTS company_grade (
                id SERIAL PRIMARY KEY,
                company_id VARCHAR(10) REFERENCES company_basic(company_id) ON DELETE CASCADE,
                year_month VARCHAR(30), year_tw VARCHAR(10), year_ad VARCHAR(10),
                import_grade VARCHAR(10), export_grade VARCHAR(10),
                fetch_date TIMESTAMP
            )"""
            )
            # 新增錯誤記錄表
            cur.execute(
                """
            CREATE TABLE IF NOT EXISTS scraping_errors (
                id SERIAL PRIMARY KEY,
                company_id VARCHAR(10),
                error_message TEXT,
                error_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                stack_trace TEXT
            )"""
            )
            conn.commit()
        logging.info("必要的資料表已創建或存在")
        return True
    except Exception as e:
        logging.error(f"建立資料表時發生錯誤：{e}")
        if conn:
            conn.rollback()
        return False


def preprocess_captcha(img: Image.Image) -> Image.Image:
    """預處理驗證碼圖片以提高辨識率"""
    img = img.convert("L").resize((img.width * 2, img.height * 2), Image.LANCZOS)
    img = ImageEnhance.Contrast(img).enhance(2.0)
    img = ImageEnhance.Sharpness(img).enhance(2.0)
    return img.point(lambda x: 255 if x > 150 else 0)


def recognize_captcha(img: Image.Image, max_attempts=3) -> str:
    """嘗試辨識驗證碼，失敗後會進行多次嘗試，支持3位數或4位數驗證碼"""
    ocr = ddddocr.DdddOcr(show_ad=False)

    for attempt in range(max_attempts):
        try:
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            res = "".join(filter(str.isdigit, ocr.classification(buf.getvalue())))

            # 如果辨識結果為3位或4位數字，直接使用
            if len(res) >= 3:
                return res[:4]  # 如果超過4位，取前4位

            # 如果第一次識別結果不夠，嘗試預處理後再試
            proc = preprocess_captcha(img)
            buf2 = io.BytesIO()
            proc.save(buf2, format="PNG")
            res2 = "".join(filter(str.isdigit, ocr.classification(buf2.getvalue())))

            # 如果預處理後的結果為3位或4位數字，使用該結果
            if len(res2) >= 3:
                return res2[:4]  # 如果超過4位，取前4位

            # 如果兩次嘗試都不足3位數，使用較長的結果
            result = res2 if len(res2) > len(res) else res
            
            # 如果結果不足3位，補0到3位
            if len(result) < 3:
                return result.ljust(3, "0")
            else:
                return result
                
        except Exception as e:
            logging.warning(f"驗證碼識別第 {attempt+1} 次失敗：{e}")
            if attempt == max_attempts - 1:
                return "000"  # 最後一次嘗試失敗，返回默認為3位數的值
            time.sleep(1)  # 暫停後再試

def setup_driver(download_dir: str, headless=True):
    """設置並返回 Selenium WebDriver"""
    opts = Options()
    if headless:
        for flag in [
            "--headless=new",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--window-size=1920,1080",
        ]:
            opts.add_argument(flag)
    
    # 添加代理設定（如果需要）
    # opts.add_argument('--proxy-server=http://your-proxy:port')
    
    download_dir = os.path.abspath(download_dir)
    prefs = {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
    }
    opts.add_experimental_option("prefs", prefs)

    try:
        # 在Docker中使用內建Chrome瀏覽器
        logging.info("嘗試直接使用Docker中的Chrome瀏覽器...")
        
        # 檢查Docker中的Chrome路徑
        chrome_paths = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable"
        ]
        
        for chrome_path in chrome_paths:
            if os.path.exists(chrome_path):
                logging.info(f"使用Chrome路徑: {chrome_path}")
                opts.binary_location = chrome_path
                break
                
        # 不使用Service類別，直接創建ChromeDriver
        driver = webdriver.Chrome(options=opts)
        driver.set_page_load_timeout(30)
        return driver
        
    except Exception as e:
        logging.error(f"初始化Chrome WebDriver失敗：{e}")
        


def save_html_to_pdf(driver, html_content, output_path, title):
    """將 HTML 內容保存為 PDF 檔案"""
    try:
        tmp = f"{os.path.dirname(output_path)}/tmp_{int(time.time())}.html"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(
                f"""<html><head><meta charset="UTF-8"><title>{title}</title></head><body>{html_content}</body></html>"""
            )
        driver.get(f"file://{os.path.abspath(tmp)}")
        time.sleep(1)
        pdf = driver.execute_cdp_cmd(
            "Page.printToPDF",
            {
                "printBackground": True,
                "paperWidth": 8.27,
                "paperHeight": 11.69,
                "marginTop": 0.4,
                "marginBottom": 0.4,
                "marginLeft": 0.4,
                "marginRight": 0.4,
                "scale": 0.8,
            },
        )
        with open(output_path, "wb") as f:
            f.write(base64.b64decode(pdf["data"]))
        os.remove(tmp)
        logging.info(f"已保存 PDF：{output_path}")
        return True
    except Exception as e:
        logging.error(f"保存 PDF 時發生錯誤：{e}")
        return False


def close_modal_dialog(driver, max_attempts=3):
    """嘗試關閉模態對話框"""
    for attempt in range(max_attempts):
        try:
            for xpath in [
                "//button[@data-dismiss='modal' and contains(., '關閉視窗')]",
                "//button[@data-dismiss='modal']",
                "//button[@aria-label='Close']",
                "//*[contains(text(),'×')]",
            ]:
                try:
                    btn = WebDriverWait(driver, 3).until(
                        EC.element_to_be_clickable((By.XPATH, xpath))
                    )
                    driver.execute_script("arguments[0].click();", btn)
                    logging.info(f"已點擊關閉按鈕：{xpath}")
                    break
                except:
                    continue

            # 等待模態背景消失
            WebDriverWait(driver, 5).until(
                EC.invisibility_of_element_located((By.CLASS_NAME, "modal-backdrop"))
            )
            return True
        except Exception as e:
            if attempt == max_attempts - 1:
                logging.warning(f"關閉模態對話框失敗：{e}")
                return False
            time.sleep(1)  # 暫停後再試


def extract_basic_data(driver):
    """擷取公司基本資料"""
    field_map = {
        "banNoM": "統一編號",
        "issueDateM": "核發日期",
        "regDateM": "原始登記日期",
        "cNameM": "廠商中文名稱",
        "eNameM": "廠商英文名稱",
        "cAdressM": "中文營業地址",
        "eAdressM": "英文營業地址",
        "regNameM": "代表人",
        "tel1M": "電話號碼1",
        "tel2M": "電話號碼2",
        "faxM": "傳真號碼",
        "oldCNameM": "原中文名稱",
        "oldENameM": "原英文名稱",
        "urlM": "網站",
        "emailM": "電子信箱",
        "importM": "進口資格",
        "exportM": "出口資格",
    }
    data = {}

    for fid, name in field_map.items():
        try:
            el = driver.find_element(By.ID, fid)
            if fid == "urlM":
                a = el.find_elements(By.TAG_NAME, "a")
                data[name] = a[0].get_attribute("href") if a else ""
            else:
                sp = el.find_elements(By.TAG_NAME, "span")
                data[name] = sp[0].text.strip() if sp else el.text.strip()
        except Exception as e:
            logging.warning(f"擷取欄位 '{name}' 時出錯：{e}")
            data[name] = ""

    # 產品項目
    for fid, name in [
        ("cStockIM", "進口項目(中)"),
        ("eStockIM", "進口項目(英)"),
        ("cStockEM", "出口項目(中)"),
        ("eStockEM", "出口項目(英)"),
    ]:
        try:
            sp = driver.find_element(By.ID, fid).find_element(By.TAG_NAME, "span")
            data[name] = sp.text.strip()
        except:
            data[name] = ""

    logging.info(f"擷取基本資料：{data}")
    return data


def extract_grade_data(driver):
    """擷取公司實績級距資料"""
    grades = []
    try:
        tbl = driver.find_element(By.CSS_SELECTOR, "#popGradeCard table.table-bordered")
        rows = tbl.find_elements(By.TAG_NAME, "tr")[3:]  # 跳過標題列

        for r in rows:
            try:
                td = r.find_elements(By.TAG_NAME, "td")
                if len(td) >= 3:
                    txt = td[0].text.strip().split("\n")
                    tw, en = txt[0], txt[1] if len(txt) > 1 else ""
                    tw_y = (
                        re.search(r"(\d+)年", tw).group(1)
                        if re.search(r"(\d+)年", tw)
                        else ""
                    )
                    ad_y = (
                        re.search(r"(\d{4})", en).group(1)
                        if re.search(r"(\d{4})", en)
                        else ""
                    )

                    grades.append(
                        {
                            "年月": tw + "/" + en,
                            "民國年": tw_y,
                            "西元年": ad_y,
                            "進口級距": td[1].text.strip(),
                            "出口級距": td[2].text.strip(),
                        }
                    )
            except Exception as e:
                logging.warning(f"解析級距資料列時發生錯誤：{e}")
                continue

        logging.info(f"擷取實績級距：{len(grades)} 筆")
        return grades
    except Exception as e:
        logging.error(f"擷取級距資料失敗：{e}")
        return []


def click_grade_button(driver, cid, max_retries=2):
    """點擊級距按鈕，帶有重試機制"""
    for retry in range(max_retries + 1):
        try:
            # 等待背景遮罩消失
            WebDriverWait(driver, 15).until(
                EC.invisibility_of_element_located((By.CLASS_NAME, "modal-backdrop"))
            )
            # 等待列表容器顯示
            WebDriverWait(driver, 15).until(
                lambda d: d.find_element(By.ID, "listContainer").is_displayed()
            )

            # 找到並點擊按鈕
            css = f"#listContainer a.btn.btn-primary[href*=\"kdbase_showPopGrade('{cid}')\"]"
            btn = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, css))
            )
            logging.info(
                f"找到級距按鈕：{btn.text if hasattr(btn, 'text') else '無文字'}"
            )

            # 嘗試點擊
            try:
                btn.click()
                logging.info("已點擊級距按鈕")
            except Exception as click_error:
                logging.warning(f"常規點擊失敗，嘗試 JavaScript 點擊：{click_error}")
                driver.execute_script("arguments[0].click();", btn)

            # 等待級距卡片顯示
            WebDriverWait(driver, 15).until(
                EC.visibility_of_element_located((By.ID, "popGradeCard"))
            )
            logging.info("級距卡片已顯示")
            return True

        except Exception as e:
            if retry < max_retries:
                logging.warning(f"點擊級距按鈕失敗，第 {retry+1} 次重試：{e}")
                time.sleep(3)  # 暫停後重試
            else:
                logging.error(f"點擊級距按鈕失敗（已重試 {max_retries} 次）：{e}")

                # 最後嘗試直接調用 JavaScript 函數
                try:
                    logging.info(
                        f"嘗試直接調用 JavaScript: kdbase_showPopGrade('{cid}')"
                    )
                    driver.execute_script(f"kdbase_showPopGrade('{cid}')")
                    WebDriverWait(driver, 10).until(
                        EC.visibility_of_element_located((By.ID, "popGradeCard"))
                    )
                    logging.info("通過 JavaScript 調用顯示級距卡片成功")
                    return True
                except Exception as js_error:
                    logging.error(f"JavaScript 調用也失敗：{js_error}")
                    return False


def fetch_grade_separately(company_id: str, download_dir: str) -> list:
    """使用單獨的 driver 獲取級距資料"""
    driver2 = None
    try:
        driver2 = setup_driver(download_dir)
        driver2.get("https://fbfh.trade.gov.tw/fb/web/queryBasicf.do")
        time.sleep(2)

        # 填寫統一編號
        id_input = WebDriverWait(driver2, 10).until(
            EC.element_to_be_clickable((By.ID, "q_BanNo"))
        )
        id_input.clear()
        id_input.send_keys(company_id)

        # 處理驗證碼
        if not handle_captcha(driver2, "verifyCode", "realPic", "querySubmit"):
            logging.error(f"[fetch_grade] 驗證碼處理失敗")
            return []

        # 點擊級距按鈕
        if not click_grade_button(driver2, company_id):
            logging.error("[fetch_grade] 點擊級距按鈕失敗")
            return []

        # 抓取級距資料
        grades = extract_grade_data(driver2)

        # 存儲級距 PDF
        save_html_to_pdf(
            driver2,
            driver2.find_element(By.ID, "popGradeCard").get_attribute("outerHTML"),
            f"{download_dir}/{company_id}_實績級距.pdf",
            "廠商實績級距",
        )

        # 關閉模態對話框
        close_modal_dialog(driver2)
        return grades

    except Exception as e:
        logging.error(f"[fetch_grade] 錯誤：{e}", exc_info=True)
        return []
    finally:
        if driver2:
            try:
                driver2.quit()
                logging.info("[fetch_grade] 第二隻 WebDriver 已關閉")
            except:
                logging.warning("[fetch_grade] 關閉第二隻 WebDriver 時出錯")


def save_data_to_postgres(conn, basic, grades, cid, status="success"):
    """將數據儲存到 PostgreSQL 資料庫"""
    if not conn:
        logging.warning("無法保存資料：資料庫連接失敗")
        return False

    try:
        now = datetime.now()
        with conn.cursor() as cur:
            # 基本資料
            fields = []
            values = []
            params = []

            # 映射數據
            field_mapping = {
                "統一編號": "company_id",
                "核發日期": "issue_date",
                "原始登記日期": "reg_date",
                "廠商中文名稱": "cn_name",
                "廠商英文名稱": "en_name",
                "中文營業地址": "cn_address",
                "英文營業地址": "en_address",
                "代表人": "representative",
                "電話號碼1": "tel1",
                "電話號碼2": "tel2",
                "傳真號碼": "fax",
                "原中文名稱": "old_cn_name",
                "原英文名稱": "old_en_name",
                "網站": "website",
                "電子信箱": "email",
                "進口資格": "import_qualification",
                "出口資格": "export_qualification",
                "進口項目(中)": "import_items_cn",
                "進口項目(英)": "import_items_en",
                "出口項目(中)": "export_items_cn",
                "出口項目(英)": "export_items_en",
            }

            # 添加基本字段
            fields.append("company_id")
            values.append("%s")
            params.append(cid)

            fields.append("fetch_date")
            values.append("%s")
            params.append(now)

            fields.append("status")
            values.append("%s")
            params.append(status)

            # 添加其他字段
            for ch_field, en_field in field_mapping.items():
                if ch_field in basic and ch_field != "統一編號":  # 統一編號已添加
                    fields.append(en_field)
                    values.append("%s")
                    params.append(basic.get(ch_field, ""))

            # 構建 SQL
            sql = f"""
            INSERT INTO company_basic ({', '.join(fields)})
            VALUES ({', '.join(values)})
            ON CONFLICT (company_id) DO UPDATE SET
            """

            # 構建 UPDATE 部分
            update_parts = []
            for field in fields:
                if field != "company_id":  # 主鍵不更新
                    update_parts.append(f"{field}=EXCLUDED.{field}")

            sql += ", ".join(update_parts)

            # 執行 SQL
            cur.execute(sql, params)

            # 存級距資料
            cur.execute("DELETE FROM company_grade WHERE company_id=%s", (cid,))
            for g in grades:
                cur.execute(
                    """
                INSERT INTO company_grade (company_id, year_month, year_tw, year_ad, import_grade, export_grade, fetch_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                    (
                        cid,
                        g.get("年月", ""),
                        g.get("民國年", ""),
                        g.get("西元年", ""),
                        g.get("進口級距", ""),
                        g.get("出口級距", ""),
                        now,
                    ),
                )

        conn.commit()
        logging.info(f"已成功保存公司 {cid} 的資料到 PostgreSQL")
        return True
    except Exception as e:
        logging.error(f"保存數據到 PostgreSQL 時發生錯誤：{e}")
        if conn:
            conn.rollback()
        return False


def log_error_to_db(conn, company_id, error_message, stack_trace=""):
    """將錯誤記錄到資料庫"""
    if not conn:
        logging.warning("無法記錄錯誤：資料庫連接失敗")
        return

    try:
        # 檢查統一編號長度，截斷如果過長
        if len(company_id) > 10:
            logging.warning(f"統一編號 '{company_id}' 過長，將被截斷")
            company_id = company_id[:10]
            
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO scraping_errors (company_id, error_message, stack_trace) VALUES (%s, %s, %s)",
                (company_id, error_message, stack_trace),
            )
            # 更新公司表狀態
            cur.execute(
                "INSERT INTO company_basic (company_id, status, fetch_date) VALUES (%s, 'error', CURRENT_TIMESTAMP) "
                "ON CONFLICT (company_id) DO UPDATE SET status='error', fetch_date=CURRENT_TIMESTAMP",
                (company_id,),
            )
        conn.commit()
        logging.info(f"已記錄公司 {company_id} 的錯誤到資料庫")
    except Exception as e:
        logging.error(f"記錄錯誤到資料庫時發生錯誤：{e}")
        if conn:
            conn.rollback()

def handle_captcha(driver, input_id, captcha_id, submit_name, cid=None, max_attempts=3):
    """處理驗證碼識別與提交，支持3位數或4位數驗證碼
    
    參數:
        driver: Selenium WebDriver 實例
        input_id: 驗證碼輸入欄位的 ID
        captcha_id: 驗證碼圖片的 ID
        submit_name: 提交按鈕的 name 屬性
        cid: 公司統一編號 (可選，用於重新填寫)
        max_attempts: 最大嘗試次數
    """
    for attempt in range(max_attempts):
        try:
            # 截取驗證碼
            pic = driver.find_element(By.ID, captcha_id)
            img = Image.open(io.BytesIO(pic.screenshot_as_png))
            code = recognize_captcha(img)
            logging.info(f"辨識的驗證碼（第 {attempt+1} 次）：{code}")

            # 輸入驗證碼
            inp = driver.find_element(By.ID, input_id)
            inp.clear()
            inp.send_keys(code)

            # 點擊查詢
            driver.find_element(By.NAME, submit_name).click()

            # 檢查結果
            try:
                # 檢查是否有錯誤訊息
                error_present = False
                try:
                    error_elem = WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located(
                            (By.XPATH, "//div[contains(@class, 'alert-danger')]")
                        )
                    )
                    error_text = error_elem.text
                    error_present = True
                    logging.warning(f"查詢錯誤：{error_text}")
                    
                    # 如果錯誤是查無資料，直接返回
                    if "查無資料" in error_text:
                        return False
                    
                except TimeoutException:
                    pass

                # 沒有錯誤訊息，檢查是否有結果
                if not error_present:
                    WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.ID, "listContainer"))
                    )
                    logging.info("✅ 驗證碼認證成功，已獲得查詢結果")
                    return True
            except TimeoutException:
                logging.warning("未找到結果容器，可能驗證碼錯誤")

            # 驗證碼可能錯誤，刷新整個頁面
            driver.refresh()
            time.sleep(2)
            
            # 重新填寫統一編號 (如果有提供)
            if cid:
                try:
                    id_input = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.ID, "q_BanNo"))
                    )
                    id_input.clear()
                    id_input.send_keys(cid)
                except:
                    logging.warning("重新填寫統一編號失敗")

        except Exception as e:
            if attempt < max_attempts - 1:
                logging.warning(f"驗證碼嘗試 {attempt+1} 失敗：{e}")
                # 刷新整個頁面
                driver.refresh()
                time.sleep(2)
                
                # 重新填寫統一編號 (如果有提供)
                if cid:
                    try:
                        id_input = WebDriverWait(driver, 5).until(
                            EC.element_to_be_clickable((By.ID, "q_BanNo"))
                        )
                        id_input.clear()
                        id_input.send_keys(cid)
                    except:
                        logging.warning("重新填寫統一編號失敗")
            else:
                logging.error(f"驗證碼嘗試達到上限 ({max_attempts} 次)")
                return False
    
    return False

def extract_company_data(cid: str, download_dir: str = "downloads", save_to_db: bool = True):
    """處理單個公司資料的主函數"""
    os.makedirs(download_dir, exist_ok=True)
    conn = connect_to_postgres() if save_to_db else None
    if conn:
        create_tables(conn)

    driver = None
    basic, grades = {}, []
    error_occurred = False
    error_message = ""

    logging.info(f"========== 開始爬取公司 {cid} 的資料 ==========")

    try:
        driver = setup_driver(download_dir)

        # --- 驗證碼 + 查詢 + 取基本資料 ---
        success = False
        max_page_attempts = 3
        
        for page_attempt in range(max_page_attempts):
            try:
                # 訪問查詢頁面
                driver.get("https://fbfh.trade.gov.tw/fb/web/queryBasicf.do")
                time.sleep(2)

                # 填寫統一編號
                id_input = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.ID, "q_BanNo"))
                )
                id_input.clear()
                id_input.send_keys(cid)
                
                # 處理驗證碼
                if handle_captcha(driver, "verifyCode", "realPic", "querySubmit"):
                    success = True
                    break
                
            except Exception as e:
                if page_attempt < max_page_attempts - 1:
                    logging.warning(f"頁面處理第 {page_attempt+1} 次失敗：{e}，將重試")
                    time.sleep(3)
                else:
                    logging.error(f"頁面處理達到最大嘗試次數：{e}")
                    error_message = f"無法成功提交查詢：{str(e)}"
                    error_occurred = True

        if not success:
            if conn:
                log_error_to_db(conn, cid, "驗證碼處理失敗或查詢無結果")
            logging.error(f"無法繼續爬取公司 {cid} 的資料：驗證碼處理失敗")
            return

        # 點擊基本資料按鈕
        try:
            btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//a[contains(@href,'kdbase_showPopBasic')]")
                )
            )
            driver.execute_script("arguments[0].click();", btn)
            WebDriverWait(driver, 10).until(
                EC.visibility_of_element_located((By.ID, "popBasicCard"))
            )
            basic = extract_basic_data(driver)

            # 保存基本資料 PDF
            save_html_to_pdf(
                driver,
                driver.find_element(By.ID, "popBasicCard").get_attribute("outerHTML"),
                f"{download_dir}/{cid}_基本資料.pdf",
                "廠商基本資料",
            )

            # 關閉模態對話框
            close_modal_dialog(driver)
        except Exception as e:
            logging.error(f"獲取基本資料時發生錯誤：{e}")
            error_message = f"獲取基本資料失敗：{str(e)}"
            error_occurred = True

        # --- 實績級距：使用第二隻 driver ---
        try:
            grades = fetch_grade_separately(cid, download_dir)
        except Exception as e:
            logging.error(f"獲取級距資料時發生錯誤：{e}")
            error_message = f"獲取級距資料失敗：{str(e)}"
            error_occurred = True

        # --- 存庫 ---
        if conn:
            if error_occurred:
                log_error_to_db(conn, cid, error_message)
                # 仍然保存已獲取的資料
                if basic:
                    save_data_to_postgres(conn, basic, grades, cid, status="partial")
            else:
                save_data_to_postgres(conn, basic, grades, cid)

    except Exception as e:
        error_message = f"爬取過程中發生錯誤：{str(e)}"
        logging.error(f"[主流程] 錯誤：{e}", exc_info=True)
        if conn:
            import traceback

            stack_trace = traceback.format_exc()
            log_error_to_db(conn, cid, error_message, stack_trace)
    finally:
        if driver:
            try:
                driver.quit()
                logging.info("主 WebDriver 已關閉")
            except:
                logging.warning("關閉主 WebDriver 時出錯")

        if conn:
            try:
                conn.close()
                logging.info("PostgreSQL 連接已關閉")
            except:
                logging.warning("關閉 PostgreSQL 連接時出錯")

        logging.info(f"========== 完成爬取公司 {cid} 的資料 ==========\n")


def batch_process(company_ids, download_dir="downloads", save_to_db=True):
    """批次處理多個公司的資料"""
    success_count = 0
    error_count = 0
    skipped_count = 0

    total = len(company_ids)
    logging.info(f"開始批次處理 {total} 個公司")

    for i, cid in enumerate(company_ids, 1):
        try:
            logging.info(f"正在處理第 {i}/{total} 個公司 (統編: {cid})")
            extract_company_data(cid, download_dir, save_to_db)
            success_count += 1
        except Exception as e:
            logging.error(f"處理公司 {cid} 時發生未捕獲的異常：{e}", exc_info=True)
            error_count += 1

        # 每處理 3 個公司暫停一下，避免被網站檢測為機器人
        if i % 3 == 0 and i < total:
            pause_time = random.randint(5, 15)
            logging.info(f"已處理 {i} 個公司，暫停 {pause_time} 秒...")
            time.sleep(pause_time)

    logging.info(
        f"""
    ===== 批次處理結果 =====
    總計: {total} 個公司
    成功: {success_count} 個
    錯誤: {error_count} 個
    跳過: {skipped_count} 個
    """
    )




def main():
    """主程式入口點"""
    import argparse
    import random

    # 診斷環境
    print_diagnostic_info()

    # 命令列參數
    p = argparse.ArgumentParser(description="爬取公司基本資料與實績級距")
    p.add_argument("company_ids", nargs="*", help="要爬取的統一編號列表")
    p.add_argument("--output", "-o", default="downloads", help="輸出目錄")
    p.add_argument("--no-db", action="store_true", help="不保存到資料庫")
    p.add_argument("--batch", "-b", action="store_true", help="批次處理預設公司列表")
    p.add_argument("--limit", "-l", type=int, default=None, help="限制處理公司數量")
    args = p.parse_args()

    companies_to_query = [
        "22178368",  # 微星科技
        "22099131",  # 台灣積體電路製造股份有限公司
        "84149961",  # 聯發科
        "22555003",  # 統一超商
        "04351626",  # 光泉牧場
        "11768704",  # 義美
        "71620635",  # 可果美
        "03707901",  # 中油
        "73008303",  # 大成長城
    ]
    
    logging.info(f"命令列參數: {args}")
    
    # 檢測腳本名稱參數
    if args.company_ids and (
        any("py" in id.lower() for id in args.company_ids) or
        any("." in id for id in args.company_ids)
    ):
        logging.warning(f"檢測到疑似腳本名稱的參數: {args.company_ids}，將使用預設公司列表")
        args.company_ids = []

    # 決定要處理的公司
    if args.batch or not args.company_ids:
        # 使用內建公司列表進行批次處理
        logging.info(f"使用預設公司列表進行批次處理")
        # 根據參數限制公司數量
        companies_to_process = companies_to_query[:args.limit] if args.limit else companies_to_query
        logging.info(f"將處理 {len(companies_to_process)} 家公司")
        batch_process(companies_to_process, args.output, not args.no_db)

    
    # 處理公司資料
    results = {}
    if len(companies_to_process) > 1:
        results = batch_process(companies_to_process, args.output, not args.no_db)
    else:
        single_result = extract_company_data(companies_to_process[0], args.output, not args.no_db)
        results = {companies_to_process[0]: single_result}
    
    # 報告結果
    logging.info("處理結果摘要:")
    for company_id, result in results.items():
        status = result.get("status", "unknown")
        basic_count = len(result.get("basic", {}))
        grades_count = len(result.get("grades", []))
        logging.info(f"公司 {company_id}: 狀態={status}, 基本資料={basic_count}項, 級距資料={grades_count}筆")
    
    return results


def print_diagnostic_info():
    """打印診斷信息用於調試"""
    logging.info("=== 環境診斷信息 ===")
    logging.info(f"Python 版本: {sys.version}")
    logging.info(f"當前目錄: {os.getcwd()}")
    logging.info(f"環境變數:")
    for key in ["POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "DISPLAY"]:
        logging.info(f"  {key}: {os.environ.get(key, '未設置')}")
    
    # 檢查 Chrome 路徑
    chrome_paths = [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/opt/google/chrome/chrome"
    ]
    for path in chrome_paths:
        if os.path.exists(path):
            logging.info(f"找到 Chrome 瀏覽器: {path}")
            try:
                version = os.popen(f"{path} --version").read().strip()
                logging.info(f"Chrome 版本: {version}")
            except:
                logging.warning(f"無法獲取 Chrome 版本")
            break
    else:
        logging.warning(f"未找到 Chrome 瀏覽器")
    
    # 檢查 ChromeDriver 路徑
    driver_paths = [
        "/usr/local/bin/chromedriver",
        "/usr/bin/chromedriver"
    ]
    for path in driver_paths:
        if os.path.exists(path):
            logging.info(f"找到 ChromeDriver: {path}")
            try:
                version = os.popen(f"{path} --version").read().strip()
                logging.info(f"ChromeDriver 版本: {version}")
            except:
                logging.warning(f"無法獲取 ChromeDriver 版本")
            break
    else:
        logging.warning(f"未找到 ChromeDriver")
    
    # 檢查 Xvfb
    try:
        xvfb_process = os.popen("ps -ef | grep Xvfb").read()
        if "Xvfb :99" in xvfb_process:
            # 修正：使用字符串的 split 方法並傳入字面量 '\n'，而不是在 f-string 中使用 '\n'
            first_line = xvfb_process.split('\n')[0]
            logging.info(f"Xvfb 正在運行: {first_line}")
        else:
            logging.warning(f"未檢測到 Xvfb 運行")
    except:
        logging.warning("無法檢查 Xvfb 狀態")



if __name__ == "__main__":
    # 定義全域 COMPANY_IDS 以便在 Jupyter 中使用
    COMPANY_IDS = [
        "22178368", # 微星科技
        "22099131", # 台灣積體電路製造股份有限公司
        "84149961", # 聯發科
        "22555003", # 統一超商
        "04351626", # 光泉牧場
        "11768704", # 義美
        "71620635", # 可果美
        "03707901", # 中油
        "73008303", # 大成長城
    ]
    
    if "ipykernel" in sys.modules:
        # 在 Jupyter Notebook 中執行
        logging.info("在 Notebook 中執行：爬取 COMPANY_IDS 中的第一個公司")
        extract_company_data(COMPANY_IDS[0])
    else:
        # 命令列執行
        main()
