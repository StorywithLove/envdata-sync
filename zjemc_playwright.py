# standard lib
import json
import time
from pathlib import Path

# internal lib
from playwright.sync_api import sync_playwright
import pandas as pd

def get_decrypted_aqi_data():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # 模拟真人浏览器（可选，但更安全）
        page.set_extra_http_headers({
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://www.baidu.com/"
        })
        # 打开页面
        page.goto("https://aqi.zjemc.org.cn/", timeout=60000)
        page.wait_for_selector(".marker_container", timeout=30000)
        
        # ===================== 【关键】点击下拉框，选择 省控站点 =====================
        try:
            page.locator(".right_header .el-input__inner").click(timeout=15000)
            page.locator("ul.el-select-dropdown__list li span", has_text="省控站点").click(timeout=15000)
            page.wait_for_selector(".right_list .list_row", timeout=20000)
            time.sleep(2)  # 确保数据完全更新
            print("✅ 已成功切换到【省控站点】")
        except Exception as e:
            print("⚠️ 切换省控站点失败，使用默认数据：", str(e))

        # 直接读取 this.result.dataList 明文数据
        data_list = page.evaluate("""() => {
            // 精准读取你断点位置的明文数据
            return window.vueInstance ? window.vueInstance.result.dataList : [];
        }""")
        
        # ===================== 获取数据（不变） =====================
        data_list = page.evaluate("""() => {
            let dataList = [];
            const all = document.querySelectorAll('*');
            for(let el of all) {
                if(el.__vue__ && el.__vue__.result && el.__vue__.result.dataList) {
                    dataList = el.__vue__.result.dataList;
                    break;
                }
            }
            return dataList;
        }""")

        # 关闭
        browser.close()
        # 输出结果
        if len(data_list) > 100: print("✅ 成功获取解密后的实时数据：")
        df = pd.DataFrame(data_list)
        df['time'] = pd.to_datetime(df.evatime) 
        df = df.drop(['evatime'], axis=1)

        timestamp = df.time.iloc[0].strftime(format="%Y-%m-%dT%H")
        daily_folder = Path('Archive')/timestamp[:10]
        daily_folder.mkdir(parents=True, exist_ok=True)
        df.to_csv(daily_folder/(timestamp+'.csv'), mode='w')
        
        return df

if __name__ == "__main__":
    """
        pip install selenium-wire selenium
        pip install selenium
        运行:
            G:\miniconda3\envs\geo\python G:\lcx\Atmos\scripts\Air_Pollution\ZJEMC\zjemc_playwright.py
    """
    print(time.strftime('%Y-%m-%d %H:%M:%S'), "开始获取数据...")
    data_df = get_decrypted_aqi_data()
    print(time.strftime('%Y-%m-%d %H:%M:%S'), "数据获取完成！")
    print(f"\n")
