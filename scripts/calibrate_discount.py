#!/usr/bin/env python3
"""券商打折系数校准器（免费代理指标版）。

方法论见 references/consensus-discount-calibration.md。
用法：python3 calibrate_discount.py <spec.json> [--md]
spec.json 字段：
  profit_series  : [[年份, 归母净利润(亿)], ...]  至少6年（算YoY需5个点）
  driver_series  : [[年份, 驱动量], ...]          可选；行业自选（发电量/销量/客流…）
  general_sigma  : float                         一般A股净利YoY波动锚（默认0.27）
输出：composite 波动率 → 建议折扣（0.05步进，截断[0.90,1.00]）+ 朴素预测回测。

v3.13：补入参体检。此前序列不足 6 年 / 全零基期会让 mean([]) 抛
StatisticsError，g<=-1 时 naive_backtest 除零，年份写成字符串会在 y0+3
处抛 TypeError；docstring 里的 --md 也一直没实现。
"""
import json
import math
import sys
from statistics import mean, stdev


class SpecError(ValueError):
    """入参体检不通过。"""


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _check_series(name, series, minimum, errors):
    """校验 [[year, value], ...]；返回规范化后的 [[int, float], ...] 或 None。"""
    if not isinstance(series, list) or len(series) < minimum:
        errors.append(f'{name} 必须是至少 {minimum} 年的 [[年份, 数值], ...]'
                      f'（当前 {type(series).__name__}，{len(series) if isinstance(series, list) else "-"} 项）')
        return None
    norm, years = [], []
    for i, row in enumerate(series, 1):
        if not (isinstance(row, (list, tuple)) and len(row) == 2):
            errors.append(f'{name} 第 {i} 项不是 [年份, 数值]：{row!r}')
            continue
        y, v = row
        if not (isinstance(y, (int, str)) and str(y).strip().lstrip('-').isdigit()):
            errors.append(f'{name} 第 {i} 项年份非法：{y!r}（需整数或数字字符串）')
            continue
        if not _is_num(v):
            errors.append(f'{name} 第 {i} 项数值非法：{v!r}（需有限数字）')
            continue
        if float(v) == 0.0:
            errors.append(f'{name} 第 {i} 项数值为 0，作为基期无法计算 YoY')
            continue
        norm.append([int(y), float(v)])
        years.append(int(y))
    if len(set(years)) != len(years):
        errors.append(f'{name} 年份重复，无法按年对齐回测')
    return norm


def validate_spec(spec):
    """返回错误列表；空列表表示可安全计算。"""
    if not isinstance(spec, dict):
        return ['spec 必须是 JSON 对象']
    errors = []
    _check_series('profit_series', spec.get('profit_series'), 6, errors)
    if spec.get('driver_series') is not None:
        _check_series('driver_series', spec['driver_series'], 3, errors)
    gs = spec.get('general_sigma', 0.27)
    if not _is_num(gs) or gs < 0:
        errors.append(f'general_sigma 必须是非负有限数字（当前 {gs!r}）')
    return errors


def yoy(series):
    """序列 → 同比增长率列表（跳过首年与零基期）。series: [[year, value], ...]"""
    out = []
    for (y0, v0), (y1, v1) in zip(series, series[1:]):
        if v0:
            out.append(v1 / v0 - 1.0)
    return out


def sigma(series):
    g = yoy(series)
    return stdev(g) if len(g) >= 2 else 0.0


def naive_backtest(profit_series, growth):
    """朴素FY+3回测：T年预测T+3 = 实际(T)×(1+g)^3；返回 [(forecast_year, ratio), ...]

    g <= -1 时 (1+g)^3 会让除数为 0/负，回测无意义 → 直接返回空列表。
    """
    if growth <= -1:
        return []
    d = dict(profit_series)
    out = []
    for (y0, v0) in profit_series:
        y3 = y0 + 3
        denom = v0 * (1 + growth) ** 3
        if y3 in d and denom:
            out.append((y3, d[y3] / denom))
    return out


def calibrate(profit_series, driver_series=None, general_sigma=0.27):
    spec = {'profit_series': profit_series, 'driver_series': driver_series,
            'general_sigma': general_sigma}
    errors = validate_spec(spec)
    if errors:
        raise SpecError('入参体检不通过：' + '；'.join(errors))
    ps = _check_series('profit_series', profit_series, 6, [])
    ds = (_check_series('driver_series', driver_series, 3, [])
          if driver_series is not None else None)
    sp = sigma(ps)
    sd = sigma(ds) if ds and len(ds) >= 3 else None
    composite = 0.6 * sp + (0.4 * sd if sd is not None else 0.0)
    raw = 1.10 - composite
    disc = min(1.00, max(0.90, round(raw * 20) / 20))
    yy = yoy(ps)
    g = mean(yy) if yy else 0.0
    bt = naive_backtest(ps, g)
    ratios = sorted(r for _, r in bt)
    p25 = ratios[max(0, round(0.25 * (len(ratios) - 1)))] if ratios else None
    res = {
        "profit_sigma": round(sp, 4), "driver_sigma": round(sd, 4) if sd is not None else None,
        "composite": round(composite, 4), "raw_discount": round(raw, 4), "discount": disc,
        "general_case_check": round(min(1.00, max(0.90, round((1.10 - general_sigma) * 20) / 20)), 2),
        "mean_growth": round(g, 4),
        "backtest": {str(y): round(r, 3) for y, r in bt},
        "backtest_p25": round(p25, 3) if p25 is not None else None,
    }
    if not yy:
        res["warning"] = '有效 YoY 样本不足，mean_growth 按 0 处理，结果仅供参考'
    if g <= -1:
        res["warning"] = '平均增速 <= -100%，朴素回测除数无效，已跳过回测'
    return res


def to_markdown(res):
    """docstring 里承诺的 --md 输出（v3.13 补齐）。"""
    lines = ['# 券商打折系数校准结果', '', '| 指标 | 值 |', '| --- | --- |']
    labels = [
        ('profit_sigma', 'σ₁ 净利 YoY 波动'), ('driver_sigma', 'σ₂ 驱动量 YoY 波动'),
        ('composite', 'composite'), ('raw_discount', '公式原始值'),
        ('discount', '**建议折扣**'), ('general_case_check', '一般 A 股锚校验'),
        ('mean_growth', '平均增速'), ('backtest_p25', '回测 P25'),
    ]
    for k, label in labels:
        v = res.get(k)
        lines.append(f'| {label} | {v if v is not None else "—"} |')
    if res.get('backtest'):
        lines += ['', '## 朴素 FY+3 回测（预测/实际）', '', '| 年份 | 比值 |', '| --- | --- |']
        for y, r in res['backtest'].items():
            lines.append(f'| {y} | {r} |')
    if res.get('warning'):
        lines += ['', '> ⚠️ ' + res['warning']]
    return '\n'.join(lines)


def main():
    argv = sys.argv[1:]
    md = '--md' in argv
    args = [a for a in argv if not a.startswith('--')]
    if not args:
        print('用法：python3 calibrate_discount.py <spec.json> [--md]', file=sys.stderr)
        return 2
    try:
        with open(args[0], encoding='utf-8') as f:
            spec = json.load(f)
        errors = validate_spec(spec)
        if errors:
            print('❌ 参数校验不通过：\n- ' + '\n- '.join(errors), file=sys.stderr)
            return 1
        res = calibrate(spec['profit_series'], spec.get('driver_series'),
                        spec.get('general_sigma', 0.27))
    except (OSError, json.JSONDecodeError, SpecError) as e:
        print('❌ ' + str(e), file=sys.stderr)
        return 1
    print(to_markdown(res) if md else json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
