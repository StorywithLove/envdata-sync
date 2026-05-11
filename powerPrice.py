import json

import requests
import pandas as pd
import matplotlib.pyplot as plt

def powerPrice_liaoning(date = '2025-03-01'):
    """
        数据自[2025-03-01, D-1]开始更新, 单位: 元/兆瓦时
    """

    url = "https://fgw.ln.gov.cn/indexview/api/getListData"
    url = "https://fgw.ln.gov.cn/indexview/api/getLine"

    # 请求体
    payload = {"date": date}

    # 请求头，尽量模拟浏览器
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://fgw.ln.gov.cn",
        "Referer": "https://fgw.ln.gov.cn/indexview",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
        # 这里可以加上浏览器中的 cookie，如果接口需要登录状态
        # "Cookie": "Path=/; Path=/; aisteUv=1778478443474580779237; aisiteJsSessionId=17784784434752990778043"
    }

    # 发送 POST 请求
    response = requests.post(url, headers=headers, data=json.dumps(payload))
    if response.status_code == 200:
        # 解析 JSON
        res_json = response.json()
        data_list = res_json.get("data", [])
        df = pd.DataFrame(data_list)
        df = df.set_index("xData")
        df = df.astype(float)
        df.index = pd.to_datetime(date + " " + df.index.str.replace("^24:", "00:", regex=True))
        mask_24 = (df.index.hour == 0) & (df.index.minute == 0)
        df.index = df.index + pd.to_timedelta(mask_24.astype(int), unit="d")

    else:
        print("请求失败，状态码:", response.status_code)
    return df

if __name__ == "__main__":
    """ 
        G:\miniconda3\envs\geo\python G:\lcx\Atmos\scripts\0000_tmp\tmp.py
    """
    cur_df = powerPrice_liaoning()
    breakpoint()
