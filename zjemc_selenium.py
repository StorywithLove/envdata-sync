# standard lib 
import os, sys
from datetime import datetime
import pdb, traceback
import random
import time
import hashlib
from pathlib import Path

# third-party lib
import pandas as pd
import numpy as np
from webdriver_manager.chrome import ChromeDriverManager
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time, json, base64, hashlib, hmac, random

# 加载浙江省生态环境监测中心的省级站点数据
def get_zjemc(web_url, usr, pwd):
    """
        获取浙江省118个省控站点的实时数据
    """
    driver = None
    try:
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")    # 设置浏览器窗口大小 
        options.add_argument("--disable-gpu")
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.get(web_url)

        # === 1. 触发省控站点 ===
        # 1.1点击[城市]
        select_btn = WebDriverWait(driver, 30).until(EC.element_to_be_clickable((By.CSS_SELECTOR, ".right_header .el-input__inner")))
        select_btn.click()
        old_text = driver.find_element(By.CSS_SELECTOR, ".right_list .list_row").text   # 记录旧数据,等待数据更新后替换

        # 1.2点击[省控站点] in [城市、区县、国控站点、省控站点]
        # option = WebDriverWait(driver, 30).until(EC.element_to_be_clickable((By.XPATH, "//li/span[text()='省控站点']")))
        option = WebDriverWait(driver, 30).until(EC.element_to_be_clickable((By.XPATH, "//ul[contains(@class,'el-select-dropdown__list')]//span[text()='省控站点']")))
        # option.click()
        time.sleep(1.0)
        # 用 JS 点击（绕过遮挡问题，最稳）
        driver.execute_script("arguments[0].click();", option)
        WebDriverWait(driver, 30).until(lambda d: d.find_element(By.CSS_SELECTOR, ".right_list .list_row").text != old_text)  # 等待数据刷新
        
        # 1.3 选择不同要素
        feat_list = ['AQI', 'SO₂', 'NO₂', 'O₃', 'PM₁₀', 'PM₂.₅', 'CO', "O₃-8h", "PM₁₀-24h", "PM₂.₅-24h"]
        factor_locator = (By.CSS_SELECTOR, ".factors_view .left_optoins.active")
        active_factor = driver.find_element(*factor_locator).text

        def get_texts(driver):
            cells = driver.find_elements(By.CSS_SELECTOR, ".right_list .list_row .list_item span")
            return [c.text for c in cells]
        def wait_for_texts_change(prev_texts, timeout=15):
            return WebDriverWait(driver, timeout, poll_frequency=0.2).until(
                lambda d: (new_texts := get_texts(d)) and new_texts != prev_texts and new_texts
            )
        cur_texts = get_texts(driver)
        current_factor = active_factor
        for factor in feat_list:
            print(factor)
            if factor != current_factor:
                factor_btn = WebDriverWait(driver, 30).until(EC.element_to_be_clickable((By.XPATH, f"//div[@class='factors_view']//div[text()='{factor}']")))
                factor_btn.click()
                cur_texts = wait_for_texts_change(cur_texts)
                current_factor = factor

            ori_df = pd.DataFrame(np.array(cur_texts).reshape(-1,5), columns=["city","site","value","level","primary"])

            if factor == feat_list[0]:
                out_df = ori_df[['site', 'city', 'value']].set_index('site')
                out_df = out_df.rename(columns={'value': factor})
            else:
                ori_df = ori_df.rename(columns={'value': factor})
                out_df = out_df.merge(ori_df[['site', factor]], on='site', how='left').set_index('site')            
        out_df = out_df.sort_values(by='city')
        file_name = driver.find_element(By.CSS_SELECTOR, ".publish_time_view span:last-of-type").text.replace(" 时", "时")
        dt = pd.to_datetime(file_name.replace("时", ""), format="%Y-%m-%d %H")
        out_df['time'] = dt
        
        # 将每小时数据保存为 csv 文件
        timestamp = dt.strftime(format="%Y-%m-%dT%H")
        daily_folder = Path('Archive')/timestamp[:10]
        daily_folder.mkdir(parents=True, exist_ok=True)
        out_df.to_csv(daily_folder/(timestamp+'.csv'), mode='w')
    except Exception as e:
        print(f"Error as: {traceback.format_exc()}")
    finally:
        if driver is not None:
            driver.quit()

if __name__ == "__main__":
    """
        pip install selenium-wire selenium
        pip install selenium
        运行:
            /root/miniconda3/envs/tf/bin/python3  /root/lcx/zjemc_selenium_server.py
        基于selenium自动测试软件, 自动登录、点击、并下载元素数据, 适用于浙江省生态环境监测中心的省级站点数据获取
    """

    web_url, usr, pwd = "https://aqi.zjemc.org.cn/#/", None, None
    hour_df = get_zjemc(web_url, usr, pwd)
    print(f"\n")
    
