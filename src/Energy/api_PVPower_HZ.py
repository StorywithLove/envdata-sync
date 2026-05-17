# standard lib
import argparse
import json
import logging
import os
import re
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

# third-party lib
import pandas as pd
import requests


SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
LOCAL_TZ = ZoneInfo("Asia/Shanghai")

STATIONS = [
    {
        "column": "欧伦",
        "station_name": "欧伦1.3835MWp分布式光伏发电系统",
        "station_id": "1299184320438401096",
    },
    {
        "column": "鸿旺",
        "station_name": "鸿旺1.582MWp分布式光伏发电系统",
        "station_id": "1299184320438147269",
    },
]


# 配置命令行和 Action 日志。
def setup_logging():
    """
    配置脚本运行日志，便于本地和 GitHub Actions 中查看执行状态。
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# 从接口返回的 Excel 单元格中提取要素信息和表格。
def extract_day_chart_df(raw_df, source="response.content"):
    """
    从已读取的 Excel 单元格 DataFrame 中提取电站要素信息和表格数据。
    """
    def after_colon(value):
        """
        提取冒号后的文本，用于解析电站名称等字段。
        """
        value = str(value).strip()
        return value.split(":", 1)[1].strip() if ":" in value else value

    def first_number(value):
        """
        从包含单位的文本中提取第一个数值。
        """
        match = re.search(r"-?\d+(?:\.\d+)?", str(value))
        return float(match.group(0)) if match else None

    header_rows = raw_df.index[raw_df.eq("PV(W)").any(axis=1)].tolist()
    if not header_rows:
        raise ValueError(f"Cannot find table header row in {source}")
    header_row = header_rows[0]

    info = {
        "title": raw_df.iat[0, 0],
        "date": re.search(r"\d{4}-\d{2}-\d{2}", raw_df.iat[0, 0]).group(0),
        "station": after_colon(raw_df.iat[2, 0]),
        "capacity_kwp": first_number(raw_df.iat[2, 3]),
        "day_energy_kwh": first_number(raw_df.iat[3, 0]),
        "income": first_number(raw_df.iat[3, 2]),
        "equivalent_hours_h": first_number(raw_df.iat[3, 4]),
    }

    table = raw_df.iloc[header_row + 1:].copy()
    table.columns = raw_df.iloc[header_row].tolist()
    table = table.loc[:, [col for col in table.columns if str(col).strip()]]
    table = table.replace("", pd.NA).dropna(how="all").reset_index(drop=True)
    return info, table


# 从接口 response.content 中直接读取 Excel 内容。
def extract_day_chart_content(content):
    """
    将接口返回的 Excel 二进制内容读入内存，并提取要素信息和表格。
    """
    raw_df = pd.read_excel(BytesIO(content), index_col=None, header=None, engine="xlrd", dtype=str).fillna("")
    return extract_day_chart_df(raw_df)


# 将单个站点的不定长功率表归一化为 96 个 15 分钟点。
def normalize_station_day_table(table, cur_date, site_name):
    """
    将单站点 PV(W) 转为 MW，并补齐为指定日期的 96 点序列；当天未来时刻保留为空值。
    """
    day_index = pd.date_range(f"{cur_date} 00:00", periods=96, freq="15min")
    station_df = table.copy()
    station_df["time"] = pd.to_datetime(cur_date + " " + station_df["时间"].astype(str))
    station_df["PV(W)"] = pd.to_numeric(station_df["PV(W)"], errors="coerce")
    station_df = station_df.dropna(subset=["time", "PV(W)"]).set_index("time").sort_index()
    station_df = station_df[["PV(W)"]].resample("15min").first().interpolate(method="time")
    station_df = station_df.reindex(day_index).fillna(0)
    station_series = (station_df["PV(W)"].astype(float) / 1000000).round(5)

    now = pd.Timestamp.now(tz=LOCAL_TZ).tz_localize(None)
    if pd.Timestamp(cur_date).date() == now.date():
        station_series.loc[station_series.index > now.floor("15min")] = pd.NA
    return station_series.rename(site_name)


# 合并两个站点的 96 点序列为最终日表。
def merge_station_day_tables(station_tables, cur_date):
    """
    合并站点序列，返回以 datetime 时间为索引、站点名称为列名的日表。
    """
    day_index = pd.date_range(f"{cur_date} 00:00", periods=96, freq="15min")
    merged_df = pd.concat(station_tables, axis=1).reindex(day_index)
    merged_df = merged_df[["鸿旺", "欧伦"]]
    merged_df.index.name = "时间"
    return merged_df


# 根据命令行参数生成待处理日期列表。
def build_date_list(start_date=None, end_date=None):
    """
    无日期参数时处理今天；一个日期参数时处理该日；两个日期参数时处理闭区间日期列表。
    """
    if not start_date:
        return [pd.Timestamp.now(tz=LOCAL_TZ).strftime("%Y-%m-%d")]
    if not end_date:
        return [pd.Timestamp(start_date).strftime("%Y-%m-%d")]

    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date)
    if start_ts > end_ts:
        raise ValueError(f"start date must be <= end date: {start_date} > {end_date}")
    return pd.date_range(start=start_ts, end=end_ts, freq="D").strftime("%Y-%m-%d").tolist()


# 登录锦浪云并捕获一次 addChart 请求模板。
def capture_add_chart_request(username, password, headless=True):
    """
    使用 Playwright 登录页面并触发导出，返回 addChart 请求的 URL、headers 和 JSON body。
    """
    from playwright.sync_api import sync_playwright

    web_url = "https://v3.ginlongcloud.com#/station/stationDetails/generalSituation/1299184320438401096"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(locale="zh-CN", timezone_id="Asia/Shanghai")
        page = context.new_page()
        page.goto(web_url, wait_until="domcontentloaded", timeout=60000)

        page.locator(".login").wait_for(timeout=30000)
        page.locator("input[placeholder='请填写手机号、邮箱或用户名']").fill(username)
        page.locator("input[placeholder='请填写密码']").fill(password)
        page.locator("label.el-checkbox input.el-checkbox__original").click(force=True)
        page.locator("div.login-btn button.el-button--primary").click(force=True)

        page.locator("div.date-select").wait_for(timeout=60000)
        export_button = page.locator("div.date-select div.station-export button").nth(1)
        export_button.scroll_into_view_if_needed()

        with page.expect_request(lambda req: "addChart" in req.url, timeout=60000) as request_info:
            export_button.click(force=True)
        request = request_info.value

        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in {"accept-encoding", "connection", "content-length", "host"}
        }
        payload = json.loads(request.post_data or "{}")
        result = {"method": request.method, "url": request.url, "headers": headers, "json": payload}
        context.close()
        browser.close()
        return result


# 使用请求模板下载指定日期的两个站点数据。
def download_day(request_template, cur_date):
    """
    调用 addChart 接口下载两个站点数据，生成固定 96 点的合并 DataFrame。
    """
    station_tables = []
    for station in STATIONS:
        payload = deepcopy(request_template["json"])
        payload["beginTime"] = cur_date
        payload["stationName"] = station["station_name"]
        payload["stationId"] = station["station_id"]

        response = requests.request(
            method=request_template["method"],
            url=request_template["url"],
            headers=request_template["headers"],
            json=payload,
            timeout=60,
        )
        if response.status_code != 200:
            logging.error("date:%s station:%s data load failed: %s", cur_date, station["column"], response.text)
            response.raise_for_status()

        info, data_df = extract_day_chart_content(response.content)
        if info["date"] != cur_date:
            raise ValueError(f"date mismatch: expected {cur_date}, got {info['date']}")
        if info["station"] != station["station_name"]:
            raise ValueError(f"station mismatch: expected {station['station_name']}, got {info['station']}")
        station_tables.append(normalize_station_day_table(data_df, cur_date, station["column"]))

    return merge_station_day_tables(station_tables, cur_date)


# 保存指定日期的合并日表。
def save_day_power(request_template, cur_date):
    """
    下载、合并并保存指定日期的杭州电站功率日表。
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    merged_df = download_day(request_template, cur_date)
    save_path = DATA_DIR / f"电站日表_{cur_date}.csv"
    merged_df.to_csv(save_path, index=True, encoding="utf-8-sig")
    logging.info("saved %s", save_path)
    return save_path


# 解析命令行参数。
def parse_args():
    """
    解析日期范围和浏览器运行模式参数。
    """
    parser = argparse.ArgumentParser(description="Download Hangzhou PV power data from GinlongCloud.")
    parser.add_argument("start_date", nargs="?", help="start date, YYYY-MM-DD; omitted means today")
    parser.add_argument("end_date", nargs="?", help="end date, YYYY-MM-DD; inclusive")
    parser.add_argument("--headed", action="store_true", help="run browser in headed mode for local debugging")
    return parser.parse_args()


# 脚本入口。
def main():
    """
    从环境变量读取账号，捕获接口请求模板，并批量下载日期列表数据。
    """
    setup_logging()
    args = parse_args()
    username = os.getenv("GLC_USR") or os.getenv("glc_usr")
    password = os.getenv("GLC_PWD") or os.getenv("glc_pwd")
    if not username or not password:
        raise EnvironmentError("Please set GLC_USR/GLC_PWD secrets or glc_usr/glc_pwd environment variables.")

    date_list = build_date_list(args.start_date, args.end_date)
    logging.info("dates: %s", ", ".join(date_list))
    request_template = capture_add_chart_request(username, password, headless=not args.headed)
    for cur_date in date_list:
        save_day_power(request_template, cur_date)


if __name__ == "__main__":
    main()
