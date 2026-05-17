import argparse
import json
import logging
import os
import re
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests


DATA_DIR = Path("Archive") / "PVPower"
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


def setup_logging():
    """
    Configure script logging for local runs and GitHub Actions.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def extract_day_chart_df(raw_df, source="response.content"):
    """
    Extract metadata and table cells from the Excel-like DataFrame.
    """
    def after_colon(value):
        """
        Return text after the first colon when present.
        """
        value = str(value).strip()
        return value.split(":", 1)[1].strip() if ":" in value else value

    def first_number(value):
        """
        Extract the first numeric value from a text field containing units.
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


def extract_day_chart_content(content):
    """
    Read the binary Excel response in memory and extract metadata/table data.
    """
    raw_df = pd.read_excel(BytesIO(content), index_col=None, header=None, engine="xlrd", dtype=str).fillna("")
    return extract_day_chart_df(raw_df)


def normalize_station_day_table(table, cur_date, site_name):
    """
    Convert one station table from PV(W) to MW and normalize it to 96 daily 15-minute points.
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


def merge_station_day_tables(station_tables, cur_date):
    """
    Merge station series into a datetime-indexed day table.
    """
    day_index = pd.date_range(f"{cur_date} 00:00", periods=96, freq="15min")
    merged_df = pd.concat(station_tables, axis=1).reindex(day_index)
    merged_df = merged_df[["鸿旺", "欧伦"]]
    merged_df.index.name = "时间"
    return merged_df


def build_date_list(start_date=None, end_date=None):
    """
    Build the target date list from optional start/end date arguments.
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


def capture_add_chart_request(username, password, headless=True):
    """
    Log into GinlongCloud with Playwright and capture one addChart request template.
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
        try:
            page.locator("label.el-checkbox").click(force=True, timeout=5000)
        except Exception:
            page.locator("label.el-checkbox input.el-checkbox__original").evaluate(
                """element => {
                    if (!element.checked) {
                        element.click();
                        element.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }"""
            )
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


def download_day(request_template, cur_date):
    """
    Download both station tables for one date and return the merged 96-point DataFrame.
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


def save_day_power(request_template, cur_date):
    """
    Download, merge, and save the Hangzhou PV power table for one date.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    merged_df = download_day(request_template, cur_date)
    save_path = DATA_DIR / f"HZ_{cur_date}.csv"
    merged_df.to_csv(save_path, index=True, encoding="utf-8-sig")
    logging.info("saved %s", save_path)
    return save_path


def parse_args():
    """
    Parse date range and browser mode arguments.
    """
    parser = argparse.ArgumentParser(description="Download Hangzhou PV power data from GinlongCloud.")
    parser.add_argument("start_date", nargs="?", help="start date, YYYY-MM-DD; omitted means today")
    parser.add_argument("end_date", nargs="?", help="end date, YYYY-MM-DD; inclusive")
    parser.add_argument("--headed", action="store_true", help="run browser in headed mode for local debugging")
    return parser.parse_args()


def main():
    """
    Read credentials from environment variables, capture request template, and download target dates.
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
