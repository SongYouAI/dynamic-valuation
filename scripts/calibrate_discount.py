#!/usr/bin/env python3
"""券商打折系数校准器（免费代理指标版）。

方法论见 references/consensus-discount-calibration.md。
用法：python3 calibrate_discount.py <spec.json> [--md]
spec.json 字段：
  profit_series  : [[年份, 归母净利润(亿)], ...]  至少6年（算YoY需5个点）
  driver_series  : [[年份, 驱动量], ...]          可选；行业自选（发电量/销量/客流…）
  driver_name    : str                           驱动量名称（仅展示）
  general_sigma  : float                         一般A股净利YoY波动锚（默认0.27）
输出：composite 波动率 → 建议折扣（0.05步进，截断[0.90,1.00]）+ 朴素预测回测。
"""
import json
import sys
from statistics import mean, stdev


def yoy(series):
    """序列 → 同比增长率列表（跳过首年）。series: [[year, value], ...]"""
    out = []
    for (y0, v0), (y1, v1) in zip(series, series[1:]):
        if v0:
            out.append(v1 / v0 - 1.0)
    return out


def sigma(series):
    g = yoy(series)
    return stdev(g) if len(g) >= 2 else 0.0


def naive_backtest(profit_series, growth):
    """朴素FY+3回测：T年预测T+3 = 实际(T)×(1+g)^3；返回 [(forecast_year, ratio), ...]"""
    d = dict(profit_series)
    out = []
    for (y0, v0) in profit_series:
        y3 = y0 + 3
        if y3 in d and v0:
            out.append((y3, d[y3] / (v0 * (1 + growth) ** 3)))
    return out


def calibrate(profit_series, driver_series=None, general_sigma=0.27):
    sp = sigma(profit_series)
    sd = sigma(driver_series) if driver_series and len(driver_series) >= 3 else None
    composite = 0.6 * sp + (0.4 * sd if sd is not None else 0.0)
    raw = 1.10 - composite
    disc = min(1.00, max(0.90, round(raw * 20) / 20))
    g = mean(yoy(profit_series))
    bt = naive_backtest(profit_series, g)
    ratios = sorted(r for _, r in bt)
    p25 = ratios[max(0, round(0.25 * (len(ratios) - 1)))] if ratios else None
    return {
        "profit_sigma": round(sp, 4), "driver_sigma": round(sd, 4) if sd is not None else None,
        "composite": round(composite, 4), "raw_discount": round(raw, 4), "discount": disc,
        "general_case_check": round(min(1.00, max(0.90, round((1.10 - general_sigma) * 20) / 20)), 2),
        "mean_growth": round(g, 4),
        "backtest": {str(y): round(r, 3) for y, r in bt},
        "backtest_p25": round(p25, 3) if p25 else None,
    }


def main():
    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    res = calibrate(spec["profit_series"], spec.get("driver_series"), spec.get("general_sigma", 0.27))
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
