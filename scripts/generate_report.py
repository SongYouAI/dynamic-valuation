#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""动态估值法 v3.2 —— 报告生成器 + 校验器

输入：JSON 参数（标的/行情/评级/取数/交叉验证结论）
输出：填充后的单文件 HTML 估值报告（仅含当前市场适配块）

依赖：仅 Python 标准库（re / json / sys / os）

v3.2 修复（对照 html-report-auditor 审计报告）：
  【正确性】
  · 三档情景「较当前」列口径统一 = 上行空间 (合理价/现价−1)，基准行不再混用 X
  · 残留占位符正则 \\{\\{([^}]+)\\}\\}（原 \\w 漏检 {{RULER_POS_X-6}} 这类表达式占位符）
  · 标尺箭头改由生成器直接算出 points 字符串，杜绝表达式占位符
  · ruler_x 加 clamp：X 超出 [-60%,120%] 量程时指针贴边而非跑出 viewBox
  【语义】
  · 评级徽章按 X 动态配色（低估绿 / 合理蓝 / 高估红），不再永远琥珀
  · 「≤52.25 ✅/❌」二选一输出；OVER_CAP 警告接入模板（原为死代码）
  · 交叉验证 7 项与结论按 ✅/⚠️/❌ 自动语义上色
  【健壮性】
  · 三档情景改「相对主参数升降一档」，数学上保证 悲观 ≤ 基准 ≤ 乐观，杜绝倒挂
    （旧规则写死「高/较好/较深」，遇超高成长股会出现乐观 < 基准的荒谬结果）
  · 柱图 Vmax 取四值最大，任何情况不溢出画布

不变的核心规则：
  · 交叉验证结论为空 → 阻断输出
  · 动态倍数 > 52.25 → 封顶 + 告警（非崩溃）
  · 9折仅作用于 FY3 有效盈利；三年分红按原始 FY 合计（与书 A公司案例一致）
"""
import re, json, sys, os

# ---------- 系数表（老板笔记钉死 + 书案例校验） ----------
BASE = {'超高': 38, '高': 30, '中高': 24, '中等': 19, '中低': 15, '低': 11, '零增长': 9, '小幅负增长': 7.5}
COMP = {'非常好': 1.1, '较好': 1.0, '较差': 0.9, '非常差': 0.8}
MOAT = {'极深': 1.25, '较深': 1.0, '一定': 0.8, '没有': 0.6}
MULT_CAP = round(38 * 1.1 * 1.25, 2)   # 52.25 倍上限（round 消除浮点误差 52.25000000000001）

# 档位有序序列（低 → 高），供三档情景「升降一档」使用
BASE_ORDER = ['小幅负增长', '零增长', '低', '中低', '中等', '中高', '高', '超高']
COMP_ORDER = ['非常差', '较差', '较好', '非常好']
MOAT_ORDER = ['没有', '一定', '较深', '极深']


def _shift(order, tier, step):
    """在有序档位序列中升/降 step 档，越界则贴边。"""
    i = order.index(tier)
    return order[max(0, min(len(order) - 1, i + step))]


# ---------- 计算核心 ----------
def calc(fy1, fy2, fy3, growth, comp, moat, cert, rf, payout, shares, price, discount=0.9):
    fy3eff = fy3 * discount
    base, c, m = BASE[growth], COMP[comp], MOAT[moat]
    raw_mult = round(base * c * m, 4)
    over_cap = raw_mult > MULT_CAP
    mult = MULT_CAP if over_cap else raw_mult          # 封顶 52.25（书：超过触发「评级过于乐观」警告，非阻断）
    disc = round(rf + (5 - cert) * 0.04, 6)
    fv3 = fy3eff * mult
    div = (fy1 + fy2 + fy3) * payout                   # 9折仅打 FY3 有效盈利；分红按原始 FY 合计（书 A公司案例一致）
    value = fv3 / ((1 + disc) ** 3) + div
    rprice = value / shares
    mktcap = price * shares
    X = mktcap / value - 1
    upside = rprice / price - 1
    return dict(mult=mult, raw_mult=raw_mult, over_cap=over_cap, disc=disc, fv3=fv3,
                div=div, value=value, rprice=rprice, X=X, upside=upside, mktcap=mktcap,
                fy3eff=fy3eff, base=base, c=c, m=m, cert=cert)


# ---------- 三档情景规则（相对主参数摆动，成文且防倒挂） ----------
# 悲观：成长/格局/护城河各降 1 档，确定性 −1（下限 0）
# 乐观：成长/格局/护城河各升 1 档，确定性 +1（上限 4）
# 数学保证：三项系数单调 + 贴现率单调 ⇒ 悲观 ≤ 基准 ≤ 乐观（封顶时取等，不倒挂）
SCEN_STEP = {'pes': -1, 'opt': +1}
CERT_STEP = {'pes': -1.0, 'opt': +1.0}


def scenario_tiers(growth, comp, moat, cert, kind):
    """返回某情景下的 (growth, comp, moat, cert)。"""
    if kind == 'base':
        return growth, comp, moat, cert
    s = SCEN_STEP[kind]
    return (_shift(BASE_ORDER, growth, s),
            _shift(COMP_ORDER, comp, s),
            _shift(MOAT_ORDER, moat, s),
            max(0.0, min(4.0, cert + CERT_STEP[kind])))


def scenario(params, kind):
    """计算某情景的 (合理股价, 上行空间)。注意第二个返回值是 upside，非 X。"""
    p = dict(params)
    cert0 = p['tech_score'] + p['cycle_score'] + p['policy_score']
    growth, comp, moat, cert = scenario_tiers(p['growth'], p['comp'], p['moat'], cert0, kind)
    r = calc(p['fy1'], p['fy2'], p['fy3'], growth, comp, moat, cert,
             p['rf_rate'], p['payout'], p['total_shares'], p['current_price'],
             p.get('consensus_discount', 0.9))
    return r['rprice'], r['upside']


def x_grade(x):
    if x < -0.50:  return '严重低估'
    if x < -0.33:  return '低估'
    if x < -0.20:  return '偏低估'
    if x <  0.25:  return '合理估值（区间 -20%~25%）'
    if x <  0.50:  return '偏高估'
    if x <  1.00:  return '高估'
    return '严重高估'


def x_grade_class(x):
    """评级徽章语义色：低估=绿(机会) / 合理=蓝(中性) / 高估=红(风险)。"""
    if x < -0.20: return 'low'
    if x <  0.25: return 'neutral'
    return 'high'


# 数据可信度档位（对齐 skill 降级机制 L1/L2/L3 → 七步财报 RICH/MEDIUM/LEAN 徽章）
DATA_TIER_MAP = {
    'L1': ('数据齐全', 'tier-rich', 'T1–T10 全量数据齐备，结论可信度最高。'),
    'L2': ('数据适中', 'tier-medium', '部分评级数据缺失，已主观补充，结论置信度降一档。'),
    'L3': ('数据稀缺', 'tier-lean', 'FY1–3 预测缺失，用历史增速外推，结论不建议单独使用。'),
}

# 评级徽章（rating-badge）语义映射：低估=买入绿 / 合理=持有金 / 高估=卖出红
RATING_CLASS_MAP = {'low': 'rating-buy', 'neutral': 'rating-hold', 'high': 'rating-sell'}


# ---------- 数值格式化 ----------
def f2(x):  return f"{x:,.2f}"
def pct(x): return f"{x*100:+.1f}%"
def pct2(x): return f"{x*100:.2f}%"


# ---------- 可视化坐标 ----------
RULER_MIN, RULER_MAX = -60.0, 120.0     # 标尺量程（%）
RULER_X0, RULER_X1 = 20.0, 740.0        # 标尺画布安全区（viewBox 0~780）


def ruler_x(xpct):
    """X(%) → 标尺横坐标，超出量程贴边（防指针跑出 viewBox）。"""
    c = max(RULER_MIN, min(RULER_MAX, xpct))
    return round(RULER_X0 + (c - RULER_MIN) * 4, 1)


def ruler_out_of_range(xpct):
    return xpct < RULER_MIN or xpct > RULER_MAX


def bar(price, Vmax):
    h = price / Vmax * 180
    return round(200 - h, 1), round(h, 1)


def sem_class(text):
    """按 ✅/⚠️/❌ 推断语义色类。"""
    t = str(text)
    if '❌' in t or '不通过' in t or '未通过' in t: return 'bad'
    if '⚠️' in t or '警告' in t or '存疑' in t:    return 'warn'
    if '✅' in t or '通过' in t:                    return 'ok'
    return ''


def wrap_sem(text):
    """给校验项文本套语义色 span（空文本原样返回）。"""
    t = str(text or '').strip()
    if not t:
        return ''
    c = sem_class(t)
    return f'<span class="{c}">{t}</span>' if c else t


# ---------- 构建占位符上下文 ----------
def build_context(p):
    cert = p['tech_score'] + p['cycle_score'] + p['policy_score']
    disc_rate = p.get('consensus_discount', 0.9)
    r = calc(p['fy1'], p['fy2'], p['fy3'], p['growth'], p['comp'], p['moat'], cert,
             p['rf_rate'], p['payout'], p['total_shares'], p['current_price'], disc_rate)
    pes_p, pes_up = scenario(p, 'pes')
    opt_p, opt_up = scenario(p, 'opt')
    cur = p['current_price']
    # 柱图纵轴峰值：取四值最大，任何情况不溢出画布
    Vmax = max(pes_p, r['rprice'], opt_p, cur) * 1.15

    # 情景档位说明（写进表格「触发条件」列，让读者知道悲观/乐观到底改了什么）
    pt = scenario_tiers(p['growth'], p['comp'], p['moat'], cert, 'pes')
    ot = scenario_tiers(p['growth'], p['comp'], p['moat'], cert, 'opt')

    ctx = {
        'COMPANY_NAME': p['company'], 'COMPANY_CODE': p['code'], 'MARKET': p['market'],
        'CURRENCY': p.get('currency', '¥'), 'REPORT_DATE': p.get('report_date', ''),
        'RF_SRC': p.get('rf_src', ''), 'RF_RATE': pct2(p['rf_rate']),
        'RF_RATE_DEC': p['rf_rate'],   # 小数形式（如 0.0172），供测算器 input 直接 parseFloat
        'CONSENSUS_SRC': p.get('consensus_src', ''),
        'CURRENT_PRICE': f2(cur), 'TOTAL_SHARES': f2(p['total_shares']),
        'CURRENT_MKTCAP': f2(r['mktcap']),
        'FY1_E': f2(p['fy1']), 'FY2_E': f2(p['fy2']), 'FY3_E': f2(p['fy3']),
        'CONSENSUS_DISCOUNT': str(disc_rate),
        'FY3_EFF_E': f2(r['fy3eff']),
        'GROWTH_TIER': p['growth'], 'BASE_MULT': f2(r['base']),
        'COMP_TIER': p['comp'], 'COMP_COEF': f2(r['c']),
        'MOAT_TIER': p['moat'], 'MOAT_COEF': f2(r['m']),
        'DYNAMIC_MULT': f2(r['mult']),
        'TECH_SCORE': f2(p['tech_score']), 'CYCLE_SCORE': f2(p['cycle_score']),
        'POLICY_SCORE': f2(p['policy_score']),
        'CERTAINTY': f2(cert), 'DISCOUNT_RATE': pct2(r['disc']),
        'FV3_MKTCAP': f2(r['fv3']), 'PAYOUT': f2(p['payout']),
        'DIV_3YR': f2(r['div']), 'REASONABLE_VALUE': f2(r['value']),
        'REASONABLE_PRICE': f"{r['rprice']:.1f}", 'X_VALUE': pct(r['X']),
        'X_GRADE': x_grade(r['X']), 'X_GRADE_CLASS': x_grade_class(r['X']),
        'X_RATING_CLASS': RATING_CLASS_MAP.get(x_grade_class(r['X']), 'rating-hold'),
        'UPSIDE': pct(r['upside']),
    }

    # ---- 数据可信度档位（L1/L2/L3 → RICH/MEDIUM/LEAN 徽章） ----
    _tier = DATA_TIER_MAP.get(p.get('data_tier', 'L1'), DATA_TIER_MAP['L1'])
    ctx['DATA_TIER_LABEL'] = _tier[0]
    ctx['DATA_TIER_CLASS'] = _tier[1]
    ctx['DATA_TIER_DESC'] = _tier[2]

    # ---- 倍数封顶：二选一输出 + 警告块（不再是死代码） ----
    if r['over_cap']:
        ctx['CAP_FLAG'] = (f'<span class="warn">原始 {f2(r["raw_mult"])} 倍 &gt; {MULT_CAP:g}，已封顶 ⚠️</span>')
        ctx['OVER_CAP_BLOCK'] = (
            '<div class="note" style="border-color:var(--amber);background:var(--amber-bg);">'
            f'⚠️ <b>倍数封顶提示</b>：三项评级相乘得 <b>{f2(r["raw_mult"])} 倍</b>，超过书中上限 '
            f'<b>{MULT_CAP:g} 倍</b>，已按上限计算。书中含义：<b>评级组合过于乐观</b>，建议回头复核成长档位与'
            '护城河判断是否过高。</div>')
    else:
        ctx['CAP_FLAG'] = f'<span class="ok">≤ {MULT_CAP:g} ✅</span>'
        ctx['OVER_CAP_BLOCK'] = ''

    # ---- 三档情景：口径统一为「较当前价上行空间」 ----
    ctx['PES_PRICE'] = f"{pes_p:.1f}"; ctx['PES_X'] = pct(pes_up)
    ctx['BASE_PRICE'] = f"{r['rprice']:.1f}"; ctx['BASE_X'] = pct(r['upside'])   # ← 修复：原误用 X（符号相反）
    ctx['OPT_PRICE'] = f"{opt_p:.1f}"; ctx['OPT_X'] = pct(opt_up)
    ctx['PES_TIERS'] = f"{pt[0]}／{pt[1]}／{pt[2]}，确定性 {pt[3]:g}"
    ctx['BASE_TIERS'] = f"{p['growth']}／{p['comp']}／{p['moat']}，确定性 {cert:g}"
    ctx['OPT_TIERS'] = f"{ot[0]}／{ot[1]}／{ot[2]}，确定性 {ot[3]:g}"

    # ---- 交叉验证（自动语义上色） ----
    cr = p.get('cross', {})
    ctx['CROSS_GROSS'] = cr.get('gross', ''); ctx['CROSS_NET'] = cr.get('net', '')
    ctx['CROSS_ROE'] = cr.get('roe', ''); ctx['CROSS_GROWTH'] = cr.get('growth', '')
    ctx['CROSS_LEVER'] = cr.get('lever', ''); ctx['CROSS_PEER'] = cr.get('peer', '')
    ctx['CROSS_HIST'] = cr.get('hist', ''); ctx['CROSS_VERDICT'] = cr.get('verdict', '')
    for i, ch in enumerate(cr.get('checks', [''] * 7), 1):
        ctx[f'CHK{i}'] = wrap_sem(ch)
    ctx['CROSS_CLASS'] = cr.get('class') or sem_class(cr.get('verdict', ''))

    # ---- 图表坐标 ----
    xp = r['X'] * 100
    rx = ruler_x(xp)
    ctx['RULER_POS_X'] = f"{rx}"
    ctx['RULER_ARROW'] = f"{rx},26 {rx - 6},14 {rx + 6},14"          # ← 修复：不再用表达式占位符
    ctx['RULER_TEXT_X'] = f"{max(58.0, min(722.0, rx))}"             # 文字防溢出
    ctx['RULER_OOR'] = '（超出量程，指针贴边）' if ruler_out_of_range(xp) else ''
    ctx['BAR_PES_Y'], ctx['BAR_PES_H'] = [f"{v}" for v in bar(pes_p, Vmax)]
    ctx['BAR_BASE_Y'], ctx['BAR_BASE_H'] = [f"{v}" for v in bar(r['rprice'], Vmax)]
    ctx['BAR_OPT_Y'], ctx['BAR_OPT_H'] = [f"{v}" for v in bar(opt_p, Vmax)]
    ctx['BAR_CUR_Y'] = f"{bar(cur, Vmax)[0]}"
    ctx['BAR_VMAX'] = f"{Vmax:.0f} {p.get('currency', '¥')}"
    ctx['DISCOUNT_NOTE'] = p.get('discount_note', '')

    # 内部体检数据（不进模板，供 validate 使用）
    ctx['_prices'] = (pes_p, r['rprice'], opt_p)
    return ctx, r


# ---------- 输出前校验（阻断 / 告警） ----------
CERT_LIMITS = {
    'tech_score':   (0.0, 1.0, '技术颠覆风险（几乎不存在1／有一定0.5／很大0）'),
    'cycle_score':  (0.0, 1.0, '行业周期波动（波动很小1／中等0.5／很大0）'),
    'policy_score': (0.0, 2.0, '政策抑制风险（极小2／有一定1／较大0）'),
}


def _g(v):
    """数字紧凑格式化（2.0→'2'、0.5→'0.5'），与前端 JS String(v) 行为对齐。"""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f'{v:g}'
    return str(v)


def validate_params(p):
    """入参范围体检：errors 阻断输出。防止确定性超满分导致贴现率为负等崩坏。"""
    errors = []
    for k, (lo, hi, desc) in CERT_LIMITS.items():
        v = p.get(k, None)
        if v is None:
            errors.append(f'确定性分项缺失：{k}（{desc}）')
            continue
        if not (lo - 1e-9 <= v <= hi + 1e-9):
            errors.append(f'确定性分项越界：{k}={_g(v)}，应在 {lo:g}~{hi:g} 之间（{desc}）')
    cert = sum(p.get(k, 0) or 0 for k in CERT_LIMITS)
    if cert > 4 + 1e-9:
        errors.append(f'确定性合计 {cert:g} 分 > 满分 4 分 → 贴现率将低于国债收益率，结果不可信')
    for k, label in (('fy1', 'FY1'), ('fy2', 'FY2'), ('fy3', 'FY3')):
        if not p.get(k, 0) or p[k] <= 0:
            errors.append(f'{label} 预期盈利必须 > 0（当前 {_g(p.get(k))}）')
    if not p.get('current_price', 0) or p['current_price'] <= 0:
        errors.append(f"当前股价必须 > 0（当前 {_g(p.get('current_price'))}）")
    if not p.get('total_shares', 0) or p['total_shares'] <= 0:
        errors.append(f"总股本必须 > 0（当前 {_g(p.get('total_shares'))}）")
    d = p.get('consensus_discount', 0.9)
    if not (0 < d <= 1.5):
        errors.append(f'一致预期折扣应在 0~1.5 之间（当前 {_g(d)}）')
    return errors


def validate(ctx):
    """返回 (errors, warnings)：errors 阻断输出，warnings 仅提示。"""
    errors, warns = [], []
    if not str(ctx.get('CROSS_VERDICT', '')).strip():
        errors.append('交叉验证结论 (CROSS_VERDICT) 为空 → 报告视为未完成，阻断输出')
    if not str(ctx.get('COMPANY_NAME', '')).strip():
        errors.append('公司名称缺失')
    pes, base, opt = ctx.get('_prices', (0, 0, 0))
    if not (pes <= base + 1e-9 <= opt + 1e-9):
        warns.append(f'三档情景非单调（悲观{pes:.1f} / 基准{base:.1f} / 乐观{opt:.1f}），请复核情景规则')
    return errors, warns


# ---------- 市场块切换（仅保留当前市场） ----------
def keep_market(html, market):
    def repl(m):
        return m.group(2) if m.group(1).strip() == market else ''
    return re.sub(r'<!-- MARKET:(.*?) -->(.*?)<!-- /MARKET -->', repl, html, flags=re.S)


# 占位符正则：允许 A-Z_0-9 之外的字符被检出（修复 {{RULER_POS_X-6}} 漏检）
PH_RE = re.compile(r'\{\{([^}\n]+)\}\}')


def set_select(html, sel_id, value):
    """在 <select id="sel_id"> 块内，给文本等于 value 的 <option> 注入 selected。
    下拉「当前项」由报告参数决定（模板里 option 不带 selected）。"""
    pat = re.compile(r'(<select id="%s".*?</select>)' % re.escape(sel_id), re.S)
    m = pat.search(html)
    if not m:
        return html
    block = m.group(1)
    newblock = re.sub(r'<option>%s</option>' % re.escape(value),
                      r'<option selected="selected">%s</option>' % value, block, count=1)
    return html[:m.start()] + newblock + html[m.end():]


# ---------- 填充并生成 ----------
def generate(params, tpl_path, out_path=None):
    pre = validate_params(params)
    if pre:
        raise SystemExit('❌ 入参体检不通过，阻断输出：\n- ' + '\n- '.join(pre))
    ctx, r = build_context(params)
    errors, warns = validate(ctx)
    if errors:
        raise SystemExit('❌ 阻断输出：\n- ' + '\n- '.join(errors))
    with open(tpl_path, encoding='utf-8') as f:
        html = f.read()
    html = keep_market(html, params['market'])
    html = PH_RE.sub(lambda m: str(ctx.get(m.group(1).strip(), '—')), html)
    # 下拉当前项：按报告参数注入 selected（测算器需要）
    html = set_select(html, 'i-growth', params['growth'])
    html = set_select(html, 'i-comp', params['comp'])
    html = set_select(html, 'i-moat', params['moat'])
    left = sorted(set(PH_RE.findall(html)))
    if out_path:
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(html)
    return html, left, warns


def main():
    inp = sys.argv[1] if len(sys.argv) > 1 else None
    if inp:
        with open(inp, encoding='utf-8') as f:
            params = json.load(f)
    else:
        params = json.loads(sys.stdin.read())
    tpl = os.path.join(os.path.dirname(__file__), '..', 'templates', 'dynamic_valuation_report_template.html')
    out = params.get('output')
    html, left, warns = generate(params, tpl, out)
    print(f"✅ 报告已生成：{out or '(未写文件)'}")
    print(f"剩余未替换占位符：{left if left else '无'}")
    for w in warns:
        print(f"⚠️  {w}")


if __name__ == '__main__':
    main()
