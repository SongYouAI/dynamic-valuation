#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""动态估值法 v3.2 —— 回归测试

运行：python3 tests/test_cases.py
覆盖：书中案例复现 / 实战复现 / 封顶边界 / 口径区分 / 市场切换 / 交叉验证阻断
      + v3.2 审计修复项：情景单调性 / 标尺 clamp / 占位符正则 / 徽章语义色 /
        封顶二选一 / 校验项语义上色 / 柱图不溢出
"""
import os, sys, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
from generate_report import (calc, scenario, scenario_tiers, x_grade, x_grade_class,
                             keep_market, generate, build_context, ruler_x, bar,
                             sem_class, wrap_sem, PH_RE, MULT_CAP,
                             RULER_X0, RULER_X1, validate_params, CERT_LIMITS)

TPL = os.path.join(ROOT, 'templates', 'dynamic_valuation_report_template.html')
passed, failed = 0, 0


def check(name, cond, detail=''):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name}  {detail}")


# ============ T1 书中 A公司案例复现 ============
print("\n【T1】书中 A公司案例（中等19 × 较差0.9 × 一定0.8 = 13.68倍，贴现率11%）")
a = calc(fy1=1.3, fy2=1.69, fy3=2.2, growth='中等', comp='较差', moat='一定',
         cert=3, rf=0.03, payout=0.30, shares=1.0, price=1.0, discount=1.0)
check("动态倍数 = 13.68", abs(a['mult'] - 13.68) < 1e-6, f"实得 {a['mult']}")
check("贴现率 = 11%", abs(a['disc'] - 0.11) < 1e-9, f"实得 {a['disc']}")
check("合理价值 ≈ 23.56 亿（书原文『约24亿』为取整）",
      abs(a['value'] - 23.56) < 0.02, f"实得 {a['value']:.4f}")

# ============ T2 宁德时代实战复现 ============
print("\n【T2】宁德时代（A股真实数据，中高24 × 较好1.0 × 一定0.8）")
n = calc(fy1=956.14, fy2=1193.31, fy3=1435.15, growth='中高', comp='较好', moat='一定',
         cert=3.0, rf=0.0171, payout=0.30, shares=46.27, price=395.30, discount=0.9)
check("动态倍数 = 19.20", abs(n['mult'] - 19.2) < 1e-6, f"实得 {n['mult']}")
check("合理股价 ≈ 429.1", abs(n['rprice'] - 429.1) < 0.15, f"实得 {n['rprice']:.2f}")
check("X ≈ -7.9%", abs(n['X'] + 0.079) < 0.002, f"实得 {n['X']*100:.2f}%")
check("上行空间 ≈ +8.6%（与 X 符号相反）", abs(n['upside'] - 0.0856) < 0.003,
      f"实得 {n['upside']*100:.2f}%")

# ============ T3 倍数封顶（warn 而非崩溃） ============
print("\n【T3】倍数封顶 52.25（超高38 × 非常好1.1 × 极深1.25 = 52.25）")
c1 = calc(1, 1, 1, '超高', '非常好', '极深', 4, 0.02, 0.3, 1, 1)
check("恰好 52.25 不触发 over_cap", not c1['over_cap'] and abs(c1['mult'] - MULT_CAP) < 1e-9)
check("MULT_CAP 常量 = 52.25", abs(MULT_CAP - 52.25) < 1e-9, f"实得 {MULT_CAP}")

# ============ T4 X 分级与徽章语义色 ============
print("\n【T4】X 分级 + 徽章语义色（低估绿 / 合理蓝 / 高估红）")
for x, g, cls in [(-0.60, '严重低估', 'low'), (-0.40, '低估', 'low'), (-0.25, '偏低估', 'low'),
                  (0.0, '合理估值（区间 -20%~25%）', 'neutral'),
                  (0.30, '偏高估', 'high'), (0.70, '高估', 'high'), (1.50, '严重高估', 'high')]:
    check(f"X={x:+.0%} → {g} / pill.{cls}",
          x_grade(x) == g and x_grade_class(x) == cls,
          f"实得 {x_grade(x)} / {x_grade_class(x)}")
check("合理估值不再固定琥珀(mid)", x_grade_class(0.0) != 'mid')

# ============ T5 市场块切换 ============
print("\n【T5】市场块真实切换")
html = ('<!-- MARKET:A股 -->AAA<!-- /MARKET -->'
        '<!-- MARKET:港股 -->HHH<!-- /MARKET -->'
        '<!-- MARKET:美股 -->UUU<!-- /MARKET -->')
for mk, keep, drop in [('A股', 'AAA', ('HHH', 'UUU')),
                       ('港股', 'HHH', ('AAA', 'UUU')),
                       ('美股', 'UUU', ('AAA', 'HHH'))]:
    out = keep_market(html, mk)
    check(f"{mk} 仅保留自身块", keep in out and all(d not in out for d in drop), f"实得 {out}")

# ============ T6 交叉验证空 → 阻断 ============
print("\n【T6】交叉验证结论为空 → 阻断输出")
base_params = dict(
    company='测试股份', code='000000', market='A股', currency='¥', report_date='2026-08-02',
    current_price=100.0, total_shares=10.0,
    fy1=10.0, fy2=12.0, fy3=15.0, consensus_discount=0.9,
    growth='中高', comp='较好', moat='一定',
    tech_score=1, cycle_score=1, policy_score=2, rf_rate=0.0175, payout=0.30,
    cross={})
try:
    generate(base_params, TPL)
    check("空结论应阻断", False, "未阻断！")
except SystemExit as e:
    check("空结论正确阻断", '交叉验证结论' in str(e), str(e))

ok_params = dict(base_params)
ok_params['cross'] = dict(verdict='✅ 通过', checks=['✅ 通过'] * 7, gross='—', net='—',
                          roe='—', growth='—', lever='—', peer='—', hist='—')
h, left, warns = generate(ok_params, TPL)
check("结论非空则正常生成", len(h) > 1000 and not left, f"残留 {left}")

# ============ T7【v3.2】三档情景单调性（防倒挂） ============
print("\n【T7】三档情景单调性：悲观 ≤ 基准 ≤ 乐观（含极端评级）")
cases = [
    ('普通中高成长', dict(growth='中高', comp='较好', moat='一定')),
    ('超高成长顶格（旧规则会倒挂）', dict(growth='超高', comp='非常好', moat='极深')),
    ('最低档贴边', dict(growth='小幅负增长', comp='非常差', moat='没有')),
    ('中等混合', dict(growth='中等', comp='较差', moat='较深')),
]
for label, tiers in cases:
    p = dict(base_params); p.update(tiers)
    cert0 = p['tech_score'] + p['cycle_score'] + p['policy_score']
    b = calc(p['fy1'], p['fy2'], p['fy3'], p['growth'], p['comp'], p['moat'], cert0,
             p['rf_rate'], p['payout'], p['total_shares'], p['current_price'], 0.9)
    pes, _ = scenario(p, 'pes')
    opt, _ = scenario(p, 'opt')
    check(f"{label}：{pes:.1f} ≤ {b['rprice']:.1f} ≤ {opt:.1f}",
          pes <= b['rprice'] + 1e-9 <= opt + 1e-9)

print("\n【T7b】情景档位升降规则正确")
check("悲观：中高→中等 / 较好→较差 / 一定→没有 / cert 4→3",
      scenario_tiers('中高', '较好', '一定', 4, 'pes') == ('中等', '较差', '没有', 3.0),
      f"实得 {scenario_tiers('中高','较好','一定',4,'pes')}")
check("乐观：中高→高 / 较好→非常好 / 一定→较深 / cert 已满分维持 4",
      scenario_tiers('中高', '较好', '一定', 4, 'opt') == ('高', '非常好', '较深', 4.0),
      f"实得 {scenario_tiers('中高','较好','一定',4,'opt')}")
check("顶格贴边不越界", scenario_tiers('超高', '非常好', '极深', 4, 'opt') == ('超高', '非常好', '极深', 4.0))
check("底档贴边不越界", scenario_tiers('小幅负增长', '非常差', '没有', 0, 'pes') == ('小幅负增长', '非常差', '没有', 0.0))

# ============ T8【v3.2】标尺 clamp（不跑出 viewBox） ============
print("\n【T8】标尺指针 clamp：任何 X 都落在安全区 [20, 740]")
for xp in (-500, -70, -60, -33, 0, 25, 120, 150, 260, 9999):
    px = ruler_x(xp)
    check(f"X={xp:>6}% → x={px:>6.1f} 在安全区", RULER_X0 - 1e-9 <= px <= RULER_X1 + 1e-9)

# ============ T9【v3.2】占位符正则不漏检 ============
print("\n【T9】占位符检测正则覆盖表达式型占位符")
s = '<polygon points="228.5,26 {{RULER_POS_X-6}},14 {{RULER_POS_X+6}},14"/> {{NORMAL}}'
found = PH_RE.findall(s)
check("能检出 {{RULER_POS_X-6}} 这类带符号占位符",
      'RULER_POS_X-6' in found and 'RULER_POS_X+6' in found and 'NORMAL' in found,
      f"实得 {found}")
check("旧正则 \\w 确实漏检（回归对照）", re.findall(r'\{\{(\w+)\}\}', s) == ['NORMAL'])

# ============ T10【v3.2】生成报告：无表达式占位符残留 + 箭头合法 ============
print("\n【T10】真实生成报告体检")
h2, left2, warns2 = generate(ok_params, TPL)
check("零残留占位符（含表达式型）", not left2, f"残留 {left2}")
poly = re.findall(r'<polygon points="([^"]+)"', h2)
check("标尺箭头 points 为合法数字坐标", bool(poly) and re.fullmatch(r'[-\d., ]+', poly[0]),
      f"实得 {poly}")
check("无倒挂告警", not warns2, f"告警 {warns2}")

print("\n【T10b】情景表三行口径一致（均为上行空间）")
ctx, r = build_context(ok_params)
pes_p, pes_up = scenario(ok_params, 'pes')
opt_p, opt_up = scenario(ok_params, 'opt')
check("BASE_X 使用 upside 而非 X",
      ctx['BASE_X'] == f"{r['upside']*100:+.1f}%" and ctx['BASE_X'] != f"{r['X']*100:+.1f}%",
      f"BASE_X={ctx['BASE_X']} upside={r['upside']*100:+.1f}% X={r['X']*100:+.1f}%")
check("三行符号方向一致（同为 price/cur−1）",
      ctx['PES_X'] == f"{pes_up*100:+.1f}%" and ctx['OPT_X'] == f"{opt_up*100:+.1f}%")

# ============ T11【v3.2】封顶二选一 + 警告块接入 ============
print("\n【T11】封顶提示二选一（不再输出「✅/❌」字面）")
p_cap = dict(ok_params); p_cap.update(growth='超高', comp='非常好', moat='极深')
h3, _, _ = generate(p_cap, TPL)
check("未超限报告：含「≤ 52.25 ✅」且无「✅/❌」字面",
      '≤ 52.25 ✅' in h2 and '✅/❌' not in h2)
check("恰好封顶不误报警告块", '倍数封顶提示' not in h3)
ctx_cap, r_cap = build_context(p_cap)
check("恰好 52.25 时 CAP_FLAG 为通过", '≤ 52.25 ✅' in ctx_cap['CAP_FLAG'], ctx_cap['CAP_FLAG'])

# ============ T12【v3.2】校验项语义上色 ============
print("\n【T12】交叉验证项按 ✅/⚠️/❌ 自动上色")
check("✅ → ok", sem_class('✅ 毛利率匹配') == 'ok')
check("⚠️ → warn", sem_class('⚠️ 略高于同业') == 'warn')
check("❌ → bad", sem_class('❌ 不通过') == 'bad')
check("wrap 包 span", wrap_sem('✅ 通过') == '<span class="ok">✅ 通过</span>')
check("空值不产生空 span", wrap_sem('') == '' and wrap_sem(None) == '')
check("报告中 CHK 已带语义类", 'class="ok"' in h2)

# ============ T13【v3.2】柱图不溢出画布 ============
print("\n【T13】柱图纵轴自适应：任何情景组合都不溢出 viewBox(0~260)")
for label, tiers, price in [
        ('超高成长', dict(growth='超高', comp='非常好', moat='极深'), 100.0),
        ('极低估（现价远低于合理价）', dict(growth='高', comp='非常好', moat='极深'), 5.0),
        ('极高估（现价远高于合理价）', dict(growth='低', comp='非常差', moat='没有'), 5000.0)]:
    p = dict(ok_params); p.update(tiers); p['current_price'] = price
    c, _ = build_context(p)
    ys = [float(c['BAR_PES_Y']), float(c['BAR_BASE_Y']), float(c['BAR_OPT_Y']), float(c['BAR_CUR_Y'])]
    check(f"{label}：所有 y 落在 [20,200]", all(19.9 <= y <= 200.1 for y in ys),
          f"实得 {ys}")

# ============ T14【v3.3】入参体检：确定性超满分等崩坏场景必须阻断 ============
print("\n【T14】入参体检 validate_params：越界必阻断，合法必放行")
check("正常参数放行", validate_params(ok_params) == [])
check("确定性分项上限定义正确（技术1/周期1/政策2，合计4）",
      CERT_LIMITS['tech_score'][1] == 1.0 and CERT_LIMITS['cycle_score'][1] == 1.0
      and CERT_LIMITS['policy_score'][1] == 2.0
      and sum(v[1] for v in CERT_LIMITS.values()) == 4.0)

for label, patch, should_block in [
        ('技术颠覆=2（上限1）',   {'tech_score': 2},          True),
        ('行业周期=2（上限1）',   {'cycle_score': 2},         True),
        ('政策抑制=3（上限2）',   {'policy_score': 3},        True),
        ('确定性正好满分 1/1/2',  {'tech_score': 1, 'cycle_score': 1, 'policy_score': 2}, False),
        ('分项取半分 0.5',        {'tech_score': 0.5},        False),
        ('FY3=0',                {'fy3': 0},                 True),
        ('FY1 为负',             {'fy1': -100},              True),
        ('股价为负',             {'current_price': -5},      True),
        ('总股本=0',             {'total_shares': 0},        True),
        ('折扣=2.0（>1.5）',      {'consensus_discount': 2.0}, True),
        ('折扣=0',               {'consensus_discount': 0},  True),
        ('折扣=1.0（不打折）',    {'consensus_discount': 1.0}, False)]:
    p = dict(ok_params); p.update(patch)
    errs = validate_params(p)
    check(f"{label} → {'阻断' if should_block else '放行'}",
          bool(errs) == should_block, f"实得 errors={errs}")

# 关键回归：确定性超满分会让贴现率跌破国债收益率（负贴现率 → 估值崩坏）
p_bad = dict(ok_params); p_bad.update({'tech_score': 2, 'cycle_score': 2, 'policy_score': 2})
cert_bad = p_bad['tech_score'] + p_bad['cycle_score'] + p_bad['policy_score']
rf_bad = p_bad['rf_rate']
disc_bad = rf_bad + (5 - cert_bad) * 0.04
check("确定性=6 会算出贴现率 < 国债利率（故必须提前阻断）",
      disc_bad < rf_bad and validate_params(p_bad) != [],
      f"cert={cert_bad} disc={disc_bad:.4f} rf={rf_bad}")

# 阻断必须发生在出报告之前
try:
    generate(p_bad, TPL)
    check("入参越界时 generate() 抛出阻断", False, "未抛异常，报告被放行")
except SystemExit as e:
    check("入参越界时 generate() 抛出阻断", '入参体检不通过' in str(e), f"实得 {e}")

# ============ T15【v3.4】数据可信度档位 + 评级徽章映射 ============
print("\n【T15】数据档位 DATA_TIER 与评级徽章 X_RATING_CLASS 映射")
from generate_report import DATA_TIER_MAP, RATING_CLASS_MAP
check("L1 → 数据齐全 / tier-rich", DATA_TIER_MAP['L1'][0] == '数据齐全' and DATA_TIER_MAP['L1'][1] == 'tier-rich')
check("L2 → 数据适中 / tier-medium", DATA_TIER_MAP['L2'][0] == '数据适中' and DATA_TIER_MAP['L2'][1] == 'tier-medium')
check("L3 → 数据稀缺 / tier-lean", DATA_TIER_MAP['L3'][0] == '数据稀缺' and DATA_TIER_MAP['L3'][1] == 'tier-lean')
check("low → rating-buy", RATING_CLASS_MAP['low'] == 'rating-buy')
check("neutral → rating-hold", RATING_CLASS_MAP['neutral'] == 'rating-hold')
check("high → rating-sell", RATING_CLASS_MAP['high'] == 'rating-sell')

ctx_t1, _ = build_context(ok_params)
check("默认 data_tier=L1 → 数据齐全 + tier-rich + 有说明",
      ctx_t1['DATA_TIER_LABEL'] == '数据齐全' and ctx_t1['DATA_TIER_CLASS'] == 'tier-rich'
      and bool(ctx_t1['DATA_TIER_DESC']), f"实得 {ctx_t1['DATA_TIER_LABEL']}/{ctx_t1['DATA_TIER_CLASS']}")
check("X_RATING_CLASS 落在 buy/hold/sell 三值", ctx_t1['X_RATING_CLASS'] in ('rating-buy', 'rating-hold', 'rating-sell'))
p_l2 = dict(ok_params); p_l2['data_tier'] = 'L2'
ctx_t2, _ = build_context(p_l2)
check("data_tier=L2 → 数据适中 + tier-medium",
      ctx_t2['DATA_TIER_LABEL'] == '数据适中' and ctx_t2['DATA_TIER_CLASS'] == 'tier-medium')
p_badtier = dict(ok_params); p_badtier['data_tier'] = 'X'
ctx_t3, _ = build_context(p_badtier)
check("无效 data_tier 回退 L1 不崩溃", ctx_t3['DATA_TIER_CLASS'] == 'tier-rich', f"实得 {ctx_t3['DATA_TIER_CLASS']}")

# ============ 汇总 ============
print("\n" + "=" * 56)
print(f"  测试结果：{passed} 通过 / {failed} 失败")
print("=" * 56)
sys.exit(1 if failed else 0)
