#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""动态估值法 v3.14 —— 回归测试

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
    cross={},
    # v3.10 出处硬约束：回归测试样例（example.com 仅用于验证链接渲染路径，非真实数据来源）
    sources=[
        dict(id='S01', use='回归测试样例', name='构造数据 · 2026-08-02',
             value='FY1 10 / FY2 12 / FY3 15 亿', caliber='测试构造，验证链接渲染',
             tier='接口', url='https://example.com/dv-regression', date='2026-08-02'),
        dict(id='S02', use='回归测试样例', name='构造数据 · 2026-08-02',
             value='无原文链接的转引条目', caliber='验证转引标注，不编造 URL',
             tier='转引', url='', date='2026-08-02'),
    ])
try:
    generate(base_params, TPL)
    check("空结论应阻断", False, "未阻断！")
except SystemExit as e:
    check("空结论正确阻断", '交叉验证结论' in str(e), str(e))

ok_params = dict(base_params)
ok_params['cross'] = dict(verdict='✅ 通过', checks=['✅ 通过'] * 7, gross='—', net='—',
                          roe='—', growth='—', lever='—', peer='—', hist='—')
# v3.5：判断链（档位选择留痕）——测试样例同时充当用法示例
_chain = [["判据", "示例判据"], ["事实与数据", "示例事实"], ["临界分析", "示例临界"], ["对估值的影响", "示例影响"]]
ok_params['growth_chain'] = _chain
ok_params['comp_chain'] = _chain
ok_params['moat_chain'] = _chain
# v3.6：确定性得分判断链 + 分红率历史依据
ok_params['cert_chain'] = _chain
ok_params['payout_note'] = '近3年实际分红率中位数（测试样例）'
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
check("无倒挂告警", not any('非单调' in w for w in warns2), f"告警 {warns2}")

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

# ============ T16【v3.5】判断链渲染 + 缺失告警 ============
print("\n【T16】判断链（档位留痕）渲染与缺失告警")
from generate_report import render_chain
ctx_c, _ = build_context(ok_params)
for key, name in (('GROWTH_CHAIN', '增长'), ('COMP_CHAIN', '竞争'), ('MOAT_CHAIN', '护城河'), ('CERT_CHAIN', '确定性')):
    check(f"{name}判断链已渲染（含判断链标题）",
          'chain-head' in ctx_c[key] and '判断链' in ctx_c[key], f"{key} 为空")
check("render_chain 空入参返回空串", render_chain(None) == '' and render_chain([]) == '')
p_nochain = dict(ok_params)
for k in ('growth_chain', 'comp_chain', 'moat_chain', 'cert_chain'):
    p_nochain.pop(k, None)
_, _, warns_nc = generate(p_nochain, TPL)
check("缺判断链 → 四条告警（不阻断）",
      sum('缺少判断链' in w for w in warns_nc) == 4, f"实得 {warns_nc}")
_, _, warns_ok = generate(ok_params, TPL)
check("带判断链 → 无链告警", not any('缺少判断链' in w for w in warns_ok), f"实得 {warns_ok}")
# 铁律五（v3.6 提出 / v3.13 加强）：无历史依据一律告警，>35% 直接阻断
p_low = dict(ok_params); p_low.pop('payout_note', None)
_, _, warns_low = generate(p_low, TPL)
check("payout=0.30 且无依据 → 分红率铁律告警（不阻断）",
      any('历史分红依据' in w for w in warns_low), f"实得 {warns_low}")
p_hi = dict(ok_params); p_hi['payout'] = 0.71; p_hi.pop('payout_note', None)
_hi_blocked, _hi_msg = False, ''
try:
    generate(p_hi, TPL)
except SystemExit as e:
    _hi_blocked, _hi_msg = True, str(e)
check("payout=0.71 且无依据 → P0-P 阻断输出",
      _hi_blocked and 'P0-P' in _hi_msg, _hi_msg[:120])
p_basis = dict(p_hi); p_basis['payout_basis'] = '近5年分红实施公告回算'
_, _, warns_basis = generate(p_basis, TPL)
check("payout_basis 可替代 payout_note 放行", not any('历史分红依据' in w for w in warns_basis))
p_ok = dict(ok_params); p_ok['payout'] = 0.71; p_ok['payout_note'] = '近3年中位71.0%'
_, _, warns_pok = generate(p_ok, TPL)
check("payout=0.71 有 payout_note → 无分红率告警",
      not any('历史分红依据' in w for w in warns_pok), f"实得 {warns_pok}")
check("PAYOUT_NOTE 缺省回退市场适配文案",
      '港股/美股见适配块' in build_context(dict(ok_params, payout_note=''))[0]['PAYOUT_NOTE']
      or 'A股默认0.30' in build_context(dict(ok_params, payout_note=None))[0]['PAYOUT_NOTE'])
# 折扣说明占位符：缺省回退书中默认文案，不出现空括号
ctx_dn, _ = build_context(ok_params)
check("DISCOUNT_NOTE 缺省回退默认文案", '默认0.9' in ctx_dn['DISCOUNT_NOTE'], ctx_dn['DISCOUNT_NOTE'])
p_dn = dict(ok_params); p_dn['discount_note'] = '校准值 0.95'
ctx_dn2, _ = build_context(p_dn)
check("DISCOUNT_NOTE 支持自定义（校准说明）", ctx_dn2['DISCOUNT_NOTE'] == '校准值 0.95')

# ============ T17 排版渲染（v3.7：长文本结构化） ============
print("\n【T17】排版渲染：一致预期小表 / 括号感知拆行 / 徽章变体符 / 校准块")
from generate_report import (render_consensus_tbl, render_clauses, split_verdict,
                             render_calib, render_cross_rows, render_verdict, short_label)

cons = {'years': ['2026E', '2027E', '2028E'],
        'platforms': [{'name': '东方财富 F10', 'cells': [[358.80, 19], [371.70, 19], [381.10, 17]]},
                      {'name': '同花顺 iFinD', 'cells': [[359.08, 21], [374.18, 21], [382.92, 20]]}],
        'adopted': [358.94, 372.94, 382.01], 'adopted_label': '采用值 · 双平台均值'}
_tbl = render_consensus_tbl(cons)
check("一致预期渲染成 prov-tbl 小表", 'class="prov-tbl"' in _tbl and _tbl.count('<tr') == 4, _tbl[:80])
check("覆盖家数进 .n 副行", '19 家' in _tbl and 'class="n"' in _tbl)
check("采纳值行高亮 adopted", 'class="adopted"' in _tbl)
check("缺 consensus 结构返回空串（走回退）", render_consensus_tbl(None) == '' and render_consensus_tbl({}) == '')

_cl = render_clauses('EPS×总股本≈净利（1.4667×244.68=358.84 vs 358.80亿；1.47×244.68=359.68 vs 359.08亿）；同花顺佐证')
check("括号内「；」不被拆开", _cl.count('class="seg"') == 2, _cl)
check("单句也包 .clauses（保证左边缘对齐）", render_clauses('只有一句')[0:0] == '' and 'clauses' in render_clauses('只有一句'))
_long = render_clauses('市场按类债资产逻辑给公司定价，股息率与国债利差交易长期存在，这属于定价体系的第一层解释。'
                       '本模型按增长速度、护城河厚度与确定性三项打分定价，这属于定价体系的第二层解释与补充说明。')
check("超 80 字长段按「。」断句", _long.count('class="seg"') == 2, _long[:200])

_chip, _body = split_verdict('⚠️ 预警：同业PE分歧')
check("⚠️ 含变体符仍取到 warn 徽章", 'vd warn' in _chip and '预警' in _chip, _chip)
check("徽章后正文剥离前缀", _body == '同业PE分歧', _body)
_chip2, _body2 = split_verdict('✅ 通过：OK')
check("✅ 取到 ok 徽章", 'vd ok' in _chip2, _chip2)
check("无前缀时徽章为空、正文原样", split_verdict('普通文本') == ('', '普通文本'))

_cal = render_calib({'title': 'T', 'items': [['a', '1'], ['b', '2']], 'note': 'N'})
check("校准块渲染 title/items/note", all(x in _cal for x in ('calib-head', 'calib-item', 'calib-note')))
check("chips 支持 current 高亮",
      'calib-chip current' in render_calib({'chips': [['2025 70.92%', False], ['采用 71.0%', True]]}))
check("render_calib 空入参返回空串", render_calib(None) == '')

_rows = render_cross_rows({'gross': '✅ 通过：甲；乙', 'peer': '⚠️ 预警：丙'})
check("交叉验证渲染 xv-row 且跳过缺失项", _rows.count('xv-row') == 2, f"{_rows.count('xv-row')}")
check("交叉验证徽章独立成行（xv-vd）", _rows.count('xv-vd') == 2)

_v = render_verdict('部分预警：甲；乙')
check("结论框拆出「部分预警」徽章", 'verdict-box' in _v and '部分预警' in _v and _v.count('class="seg"') == 2, _v[:120])
check("结论框空入参返回空串", render_verdict('') == '')

check("short_label 截到首个短语", short_label('东方财富F10盈利预测（很长很长很长的括号内容）') == '东方财富F10盈利预测')
check("short_label 空值返回破折号", short_label('') == '—')

# ============ T18【v3.9】HYDRO PAPER v3.0 皮肤门禁 ============
print("\n【T18】HYDRO PAPER v3.0 皮肤：三层 CSS / 页脚层级 / 自包含 / classic 开关")
import generate_report as G

h_skin, left_skin, _ = generate(dict(ok_params), TPL)
check("三层 CSS 标记在位",
      all(x in h_skin for x in ('id="hydro-skin"', 'id="dynamic-adapt"',
                                'data-design="HYDRO PAPER v3.0"')))
check("CSS 注入顺序：皮肤 → 适配层",
      h_skin.index('id="hydro-skin"') < h_skin.index('id="dynamic-adapt"'))
check("旧单行署名 footer 已移除", '<footer>动态估值报告模板' not in h_skin)
check("brand 页脚在位", '<footer class="report-footer">' in h_skin)
_fp = G._FooterDepth()
_fp.feed(h_skin)
check("页脚是 body 直接子元素、不嵌在 .wrap 内", _fp.seen and not _fp.inside_wrap)
_e, _w = G.validate_hydro(h_skin)
check("validate_hydro P0-A~P0-H 全过", not _e, str(_e))
check("两张二维码 base64 内嵌", h_skin.count('data:image/png;base64,') >= 2)
# v3.10：门禁只禁远程「资源」；<a href="https://..."> 是可核查出处，放行。
_remote_res = re.compile(
    r'<(?:img|script|iframe|video|audio|source|link)\b[^>]*\b(?:src|href)\s*=\s*["\']https?://'
    r'|@import\s+[^;]*https?://|url\(\s*["\']?https?://')
check("零远程资源引用（出处 <a href> 放行）", _remote_res.search(h_skin) is None)
check("零同目录 assets 引用",
      'src="assets/' not in h_skin and 'href="assets/' not in h_skin)
check("测算器 / 灯箱关键 id 未丢",
      all(('id="%s"' % i) in h_skin for i in ('h-price', 'v-fy3eff', 'btn-apply', 'qrLightbox')))
check("固定免责声明在位", '以上内容基于公开数据和量化分析' in h_skin)
check("皮肤注入后占位符零残留", not left_skin, str(left_skin))
check("Hero 等高线已注入且压回绝对定位",
      'class="hero-art"' in h_skin and 'svg.hero-art' in G._asset('dynamic-adapt.css'))
_p_cls = dict(ok_params)
_p_cls['skin'] = 'classic'
h_cls, left_cls, _ = generate(_p_cls, TPL)
check("skin=classic 关闭三层注入与 brand 页脚",
      'id="hydro-skin"' not in h_cls and '<footer class="report-footer">' not in h_cls)
check("classic 仍保留旧署名 footer", '<footer>动态估值报告模板' in h_cls)

# ============ T19【v3.9】测算器 input 全精度（重置一致性） ============
print("\n【T19】测算器 input 全精度：重置后与报告正文逐位一致")
_ctx, _ = build_context(ok_params)
check("ctx 含 6 个 RAW 全精度字段",
      all(k in _ctx for k in ('FY1_RAW', 'FY2_RAW', 'FY3_RAW',
                              'SHARES_RAW', 'PAYOUT_RAW', 'PRICE_RAW')))
check("_raw 去尾零", G._raw(15.0) == '15' and G._raw(0.3280, 6) == '0.328')
h_prec, left_prec, _ = generate(dict(ok_params), TPL)


def _val(s, i):
    m = re.search(r'<input id="%s"[^>]*value="([^"]+)"' % i, s)
    return m.group(1) if m else None


check("i-fy3 = 服务端 FY3 全精度",
      _val(h_prec, 'i-fy3') is not None
      and abs(float(_val(h_prec, 'i-fy3')) - ok_params['fy3']) < 1e-6)
check("i-shares = 服务端总股本全精度",
      _val(h_prec, 'i-shares') is not None
      and abs(float(_val(h_prec, 'i-shares')) - ok_params['total_shares']) < 1e-8)
check("i-payout = 服务端支付率全精度",
      _val(h_prec, 'i-payout') is not None
      and abs(float(_val(h_prec, 'i-payout')) - ok_params['payout']) < 1e-6)
check("新增 RAW 占位符零残留", not left_prec, str(left_prec))

# ============ T20【v3.10】侧栏显隐 / 首列断字 / 出处体系 ============
print("\n【T20】v3.10：侧栏首屏隐藏 · 表格首列不断字 · 出处六元组")
h20, left20, _ = generate(dict(ok_params), TPL)
check("基线含 sources 且编号唯一",
      [s['id'] for s in ok_params['sources']] == ['S01', 'S02'])
_p = dict(ok_params); _p.pop('sources')
check("sources 为空 → validate_params 阻断",
      any('出处缺失' in e for e in validate_params(_p)))
_p2 = dict(ok_params); _p2['sources'] = [dict(ok_params['sources'][0], url='ftp://x')]
check("url 非 http(s) → validate_params 阻断",
      any('url 非 http' in e for e in validate_params(_p2)))
check("侧栏初始带 nav-hidden", 'class="side-nav nav-hidden"' in h20)
check("侧栏滚动显隐 JS 在位", "classList.toggle('nav-hidden'" in h20)
check("侧栏与目录均有出处锚点",
      'href="#sec-sources"' in h20 and h20.count('href="#sec-sources"') >= 2)
check("判断链首列加宽且 nowrap（不再断字）",
      '.chain-tbl th{ width:172px; min-width:172px; white-space:nowrap;' in h20)
check("取数口径行首列 176px 且标签可换行（不再压进值列）",
      'grid-template-columns:176px minmax(0,1fr)' in h20
      and '.prov-k{white-space:normal;overflow-wrap:anywhere;min-width:0;' in h20)
check("数据来源汇总章节 + 卡片列表在位",
      'id="sec-sources"' in h20 and 'class="src-list"' in h20
      and 'class="src-card-fields"' in h20)
check("卡片数 = sources 条数，旧窄列表格零残留",
      h20.count('class="src-card"') == len(ok_params['sources'])
      and 'class="src-tbl"' not in h20)
check("P0-H：旧窄列 .src-tbl 被阻断",
      any('旧窄列' in e for e in G.validate_hydro(
          h20.replace('class="src-list"', 'class="src-tbl"'))[0]))
check("出处链接带 target=_blank / rel=noopener",
      'href="https://example.com/dv-regression" target="_blank" rel="noopener noreferrer"' in h20)
check("无原文链接的条目显式标注转引", '转引 · 未取得原文链接' in h20)
_e20, _w20 = G.validate_hydro(h20)
check("validate_hydro P0-A~P0-H 全过", not _e20, str(_e20))
check("P0-E 收窄：远程 <a> 出处链接放行（远程资源仍禁止）",
      not any('P0-E' in e for e in G.validate_hydro(h20)[0])
      and any('P0-E' in e for e in G.validate_hydro(
          h20.replace('</body>', '<img src="https://x/y.png"></body>'))[0]))
check("P0-H：外部链接缺 noopener 被阻断",
      any('rel=noopener' in e for e in G.validate_hydro(
          h20.replace('rel="noopener noreferrer"', 'rel=""'))[0]))
check("P0-H：href=\"#\" 空链接被阻断",
      any('空链接' in e for e in G.validate_hydro(
          h20.replace('</body>', '<a href="#">x</a></body>'))[0]))
check("生成无占位符残留", not left20, str(left20))

# ============ T21【v3.12】审查修复回归 ============
print("\n【T21】v3.12：入参体检 / 占位符真门禁 / 测算器前后端一致 / 发布包")
import tempfile


def _expect_block(name, mutate, keyword):
    _p = dict(ok_params)
    mutate(_p)
    try:
        generate(_p, TPL)
        check(name, False, '未阻断')
    except SystemExit as e:
        check(name, keyword in str(e), str(e)[:140])
    except Exception as e:
        check(name, False, f'未友好阻断，抛 {type(e).__name__}: {e}')


# ① 未定义占位符过去被静默替换成破折号，替换后再 findall 恒为空（假门禁）。
_typo = open(TPL, encoding='utf-8').read().replace(
    '{{COMPANY_NAME}}', '{{COMPANY_NAME}} {{TYPO_UNDEFINED_KEY}}', 1)
_fd, _tmp = tempfile.mkstemp(suffix='.html')
os.close(_fd)
try:
    with open(_tmp, 'w', encoding='utf-8') as _f:
        _f.write(_typo)
    try:
        generate(dict(ok_params), _tmp)
        check("模板未定义占位符 → 阻断", False, '未阻断，会静默渲染成破折号')
    except SystemExit as e:
        check("模板未定义占位符 → 阻断", 'TYPO_UNDEFINED_KEY' in str(e), str(e)[:140])
finally:
    os.unlink(_tmp)

# ② 缺字段 / 非法值过去抛 KeyError、TypeError，或静默删光市场适配块。
_expect_block("缺 rf_rate → 友好阻断", lambda p: p.pop('rf_rate'), 'rf_rate')
_expect_block("缺 growth → 友好阻断", lambda p: p.pop('growth'), 'growth')
_expect_block("fy1 传字符串 → 友好阻断", lambda p: p.update(fy1='12'), '必须为数字')
_expect_block("growth 档位非法 → 阻断", lambda p: p.update(growth='未知档位'), '档位非法')
_expect_block("market 非法 → 阻断", lambda p: p.update(market='新三板'), 'market 非法')
_expect_block("rf_rate 负值 → 阻断", lambda p: p.update(rf_rate=-0.05), 'rf_rate 无风险利率异常')
_expect_block("payout 负值 → 阻断", lambda p: p.update(payout=-0.3), 'payout 支付率异常')
_expect_block("payout > 100% → 阻断", lambda p: p.update(payout=1.8), 'payout 支付率异常')

# ③ 测算器 key 与控件 id 后缀必须一一对应（重置循环用 $('i-'+k)）。
_tpl_src = open(TPL, encoding='utf-8').read()
_rp = re.search(r'function readParams\(\)\{return \{(.*?)\};\}', _tpl_src, re.S)
_keys = set(re.findall(r'(\w+)\s*:\s*(?:num|txt)\(', _rp.group(1))) if _rp else set()
_ids = {x[2:] for x in re.findall(r'<(?:input|select)[^>]*id="(i-[^"]+)"', _tpl_src)}
check("readParams key 与 i-* 控件一一对应（重置不再漏折扣率）",
      bool(_keys) and _keys == _ids, f'key={sorted(_keys)} id={sorted(_ids)}')
check("折扣率 key 为 disc（匹配 i-disc）", "disc:num('i-disc')" in _tpl_src)

# ④ 前端封顶/评级文案/X 符号与服务端逐位一致。
_js = re.findall(r'<script[^>]*>(.*?)</script>', _tpl_src, re.S)[-1]
check("前端倍数 round4，消除 52.25000000000001 误触顶",
      'Math.round(base*c*m*10000)/10000' in _js and 38 * 1.1 * 1.25 > 52.25)
_c3 = calc(1, 1, 1, '超高', '非常好', '极深', 4, 0.02, 0.3, 1, 1)
check("服务端同组合不触顶（前后端判据一致）", not _c3['over_cap'])
check("前端评级文案与 x_grade() 一致",
      x_grade(0.0) in _js, f"服务端 {x_grade(0.0)}")
check("X 显示带符号 sp()，与服务端 pct() 一致",
      _js.count('sp(r.X)') == 6 and 'p1(r.X)' not in _js)

# ⑤ 发布包必须含 assets/，否则远端 clone 的生成器直接 FileNotFoundError。
_sync = open(os.path.join(ROOT, 'sync.sh'), encoding='utf-8').read()
check("sync.sh 同步 assets/ 并做必需文件检查",
      'for d in scripts templates tests assets' in _sync
      and 'hydro-skin.css dynamic-adapt.css brand.json' in _sync)

# ============ T22【v3.13】7 项待定优化：净化 / 引用门禁 / 铁律 / 注入 / 容错 / 校准 / 文档 ============
print("\n【T22】v3.13：富文本净化 · 出处引用一致性 · 分红铁律 · 注入定位 · 测算器容错 · 校准器体检 · 文档边界")
import subprocess
import tempfile
import generate_report as G
import calibrate_discount as C

# ① 白名单净化器
_t1 = G.sanitize_rich('X < -0.5 且 ROE>15% 与 A & B')
check("裸 < / > / & 全部转义", '&lt;' in _t1 and '&gt;' in _t1 and '&amp;' in _t1, _t1)
_link = ('东财 <a class="ref" href="https://example.com/a?p=1&amp;q=2" '
         'target="_blank" rel="noopener">[S09]</a> 收盘')
_t2 = G.sanitize_rich(_link)
check("合法 [S##] 出处上标保留", '<a ' in _t2 and '[S09]</a>' in _t2
      and 'class="ref"' in _t2 and 'https://example.com/a?p=1&amp;q=2' in _t2, _t2)
check("出处链接自动补 target / rel", 'target="_blank"' in _t2 and 'noopener noreferrer' in _t2)
_t3 = G.sanitize_rich('<a href="javascript:alert(1)">危险</a>')
check("javascript: 链接降级为纯文本容器", '<span>' in _t3 and 'javascript:' not in _t3, _t3)
_t4 = G.sanitize_rich('<script>alert(1)</script><b>粗体</b>')
check("<script> 被转义、基础排版标签保留", '<script' not in _t4 and '&lt;script&gt;' in _t4
      and '<b>粗体</b>' in _t4, _t4)
check("净化器幂等（可对已净化文本重复调用）",
      all(G.sanitize_rich(G.sanitize_rich(x)) == G.sanitize_rich(x)
          for x in (_t1, _link, _t3, _t4)))
check("未闭合 <a> 自动补齐", G.sanitize_rich('<a href="https://a.com">[S02]').endswith('</a>'))
check("render_clauses 净化输入", '&lt;script&gt;' in G.render_clauses('<script>x</script>'))
check("render_chain / render_calib / render_prov_rows 净化输入",
      '<script' not in G.render_chain([['k', '<script>x</script>']])
      and '<script' not in G.render_calib({'note': '<script>x</script>'})
      and '<script' not in G.render_prov_rows([['k', '<script>x</script>']]))

# ② 生成器端到端：危险输入不破坏结构，合法出处链保留
_p_rich = dict(ok_params)
_p_rich['company'] = '测试<script>alert(1)</script>股份'
_p_rich['consensus_src'] = ('东财 F10 <a class="ref" href="https://example.com/x" '
                            'target="_blank" rel="noopener">[S01]</a>；转引 [S02]')
_p_rich['sources'] = [dict(s) for s in ok_params['sources']] + [
    dict(id='S09', use='研报上标', name='构造数据 · 2026-08-02',
         value='净化链路测试', caliber='测试构造，验证出处上标保留',
         tier='接口', url='https://example.com/dv-s09', date='2026-08-02')]
_p_rich['growth_chain'] = [['判据', '含裸 < 与 & 符号'], ['事实', _link]]
_h_rich, _left_rich, _ = generate(_p_rich, TPL)
check("company 含 <script> 不注入可执行标签",
      '<script>alert(1)</script>' not in _h_rich and '&lt;script&gt;' in _h_rich)
check("净化后正文仍保留 [S09] 上标链接", '[S09]</a>' in _h_rich)
check("净化后无占位符残留、P0-A~P0-H 零错",
      not _left_rich and not G.validate_hydro(_h_rich)[0], str(G.validate_hydro(_h_rich)[0]))

# ③ 出处引用 ↔ sources 编号一致性
_src_ok = dict(_p_rich)
check("正文引用 [S01]/[S02] 均在 sources 中 → 放行",
      not G.source_refs_audit(_src_ok)[0], str(G.source_refs_audit(_src_ok)[0]))
_bad_ref = dict(_src_ok); _bad_ref['consensus_src'] = '研报 [S99] 未登记'
check("引用未定义编号 [S99] → 阻断",
      any('不存在的编号' in e for e in G.source_refs_audit(_bad_ref)[0]))
_bad_id = dict(_src_ok); _bad_id['sources'] = [dict(s) for s in _src_ok['sources']]
_bad_id['sources'][0]['id'] = 'A1'
check("sources id 非 S\\d{2,} → 阻断",
      any('编号格式非法' in e for e in G.source_refs_audit(_bad_id)[0]))
_bad_tier = dict(_src_ok); _bad_tier['sources'] = [dict(s) for s in _src_ok['sources']]
_bad_tier['sources'][0]['url'] = ''; _bad_tier['sources'][0]['tier'] = '接口'
check("无链接却未标「转引」→ 阻断",
      any('tier 必须标' in e for e in G.source_refs_audit(_bad_tier)[0]))
_warn_tier = dict(_src_ok); _warn_tier['sources'] = [dict(s) for s in _src_ok['sources']]
_warn_tier['sources'][0]['tier'] = '转引'
check("有链接却标 tier=转引 → 告警",
      any('tier=转引' in w for w in G.source_refs_audit(_warn_tier)[1]))
_unused = dict(ok_params)
_unused_w = G.source_refs_audit(_unused)[1]
check("未被引用出处合并为一条提示（不阻断）",
      len(_unused_w) == 1 and 'S01' in _unused_w[0] and 'S02' in _unused_w[0], str(_unused_w))
check("引用扫描排除 sources 自身与 url / output 字段",
      not G._collect_refs({'sources': [{'id': 'S01', 'use': '[S77]'}],
                           'url': '[S88]', 'output': '[S99]'}))
_try_ref = dict(_src_ok); _try_ref['consensus_src'] = '研报 [S99]'
_ref_blocked = False
try:
    generate(_try_ref, TPL)
except SystemExit as e:
    _ref_blocked = '不存在的编号' in str(e)
check("generate() 真阻断无出处引用", _ref_blocked)

# ④ 分红率铁律三态
_e1, _w1 = G.payout_basis_audit({'payout': 0.30})
_e2, _w2 = G.payout_basis_audit({'payout': 0.71})
_e3, _w3 = G.payout_basis_audit({'payout': 0.71, 'payout_basis': '近5年分红实施公告回算'})
check("无依据 0.30 → 告警不阻断", not _e1 and len(_w1) == 1)
check("无依据 0.71 → P0-P 阻断", len(_e2) == 1 and 'P0-P' in _e2[0])
check("payout_basis 可替代 payout_note 放行", not _e3 and not _w3)
check("PAYOUT_NOTE 可用 payout_basis 回退",
      '台账' in G.build_context(dict(ok_params, payout_note='', payout_basis='台账核对'))[0]['PAYOUT_NOTE'])

# ⑤ apply_skin 注入定位 + P0-A 位置检查
_tpl_src = open(TPL, encoding='utf-8').read()
_tpl_with_head_literal = _tpl_src.replace('<style>', '<!-- 注释里含字面 </head> -->\n<style>', 1)
_skin_html = G.apply_skin(_tpl_with_head_literal)
check("head 注释含字面 </head> 时 CSS 仍落在 <body> 前",
      0 <= _skin_html.find('id="hydro-skin"') < _skin_html.find('<body'))
check("三层 CSS 落在 body 之后 → P0-A 阻断",
      any('被注入到 <body> 之后' in e
          for e in G.validate_hydro('<html><head></head><body><style id="hydro-skin"></style></body></html>')[0]))
_no_head = False
try:
    G.apply_skin('<html><body>x</body></html>')
except SystemExit as e:
    _no_head = '缺少 </head>' in str(e)
check("模板无 </head> → 友好阻断", _no_head)

# ⑥ 校准器入参体检
_good = [[2016 + i, 10 + 2 * i + ((-1) ** i) * 0.6] for i in range(10)]
_cases_bad = {
    '不足 6 年': {'profit_series': [[2020, 10], [2021, 11]]},
    '零基期': {'profit_series': [[2016, 0], [2017, 1], [2018, 2], [2019, 3], [2020, 4], [2021, 5]]},
    '非数字': {'profit_series': [[2016, 'x'], [2017, 11], [2018, 12], [2019, 13], [2020, 14], [2021, 15]]},
    '重复年份': {'profit_series': [[2016, 10]] * 6},
    '锚非法': {'profit_series': _good, 'general_sigma': 'abc'},
}
for _name, _spec in _cases_bad.items():
    check(f"校准器阻断：{_name}", bool(C.validate_spec(_spec)))
check("字符串年份兼容（y0+3 不再 TypeError）",
      C.calibrate([[str(2016 + i), 10 + i] for i in range(6)])['discount'] >= 0.90)
check("g <= -1 回测不再除零", C.naive_backtest(_good, -1.0) == [])
check("校准器 --md 输出已实现", '# 券商打折系数校准结果' in C.to_markdown(C.calibrate(_good)))
with tempfile.NamedTemporaryFile('w', suffix='.json', encoding='utf-8', delete=False) as _tf:
    import json as _json
    _json.dump({'profit_series': _good}, _tf)
    _spec_path = _tf.name
_md = subprocess.run([sys.executable, os.path.join(ROOT, 'scripts', 'calibrate_discount.py'),
                      _spec_path, '--md'], capture_output=True, text=True)
check("CLI --md 返回 0 且输出 Markdown", _md.returncode == 0 and _md.stdout.startswith('# '),
      _md.stderr[:120])
os.unlink(_spec_path)

# ⑦ 测算器 JS 容错（静态）+ 发布包 / 文档边界
_js313 = re.findall(r'<script[^>]*>(.*?)</script>', _tpl_src, re.S)[-1]
check("num() 不再 ||0 静默归零", 'parseFloat($(id).value)||0' not in _js313
      and 'Number.isFinite(v)?v:NaN' in _js313)
check("输入体检 sanitize() + 提示 showCalcWarn() 存在",
      'function sanitize(p)' in _js313 and 'function showCalcWarn(keys)' in _js313)
check("calc() 分母钳制（shares / price / denom）",
      'Math.max(1e-9,p.shares)' in _js313 and 'denom>0?denom:1e-9' in _js313
      and 'p.price>0?rprice/p.price-1:0' in _js313)
check("柱状图 Vmax 不为 0", 'Math.max(1e-9,Math.max(pes.rprice' in _js313)
check(".calc-warn 样式与重置清提示", '.calc-warn' in _tpl_src and 'showCalcWarn([])' in _js313)
check("templates/ 下无 .bak 历史备份",
      not [f for f in os.listdir(os.path.join(ROOT, 'templates')) if '.bak' in f])
# v3.14：temp/ 被 .gitignore 排除，发布包（GitHub clone / SkillHub zip）里根本没有
# 这个目录。旧断言直接 os.listdir 会抛 FileNotFoundError，让别人的回归测试必崩。
# 契约改成：要么干净发布包（0 个备份），要么本地开发目录（历史 5 个备份），
# 不允许出现「备份散落别处」的中间态。
_tmp_dir = os.path.join(ROOT, 'temp')
_tmp_baks = ([f for f in os.listdir(_tmp_dir) if '.bak' in f]
             if os.path.isdir(_tmp_dir) else [])
check("备份归入 temp/，且发布包无此目录也能跑",
      len(_tmp_baks) in (0, 5))
check("发布包内无散落的 .bak 备份",
      not [f for d in ('scripts', 'templates', 'tests', 'assets')
           for f in os.listdir(os.path.join(ROOT, d)) if '.bak' in f])
_gi = open(os.path.join(ROOT, '.gitignore'), encoding='utf-8').read()
check("references/ 与 temp/ 仍在 .gitignore",
      'references/' in _gi and 'temp/' in _gi)
check(".gitignore 仍拦 *.bak* 备份", '*.bak*' in _gi)
_skill = open(os.path.join(ROOT, 'SKILL.md'), encoding='utf-8').read()
_readme = open(os.path.join(ROOT, 'README.md'), encoding='utf-8').read()
check("SKILL.md / README 均写明 references 文档边界",
      '文档边界（v3.13）' in _skill and '不读 `references/` 也能完整运行' in _readme)
# v3.14：SkillHub 要求合法 SemVer（三段式），frontmatter 用 3.14.0；
# 正文/文档的人类可读标签仍是 v3.14，两者由本断言锁定，避免以后改漏。
check("版本串四处一致（frontmatter 3.14.0 / 标签 v3.14）",
      'version: 3.14.0' in _skill and 'v3.14' in _readme
      and 'v3.14' in open(os.path.join(ROOT, 'scripts', 'generate_report.py'), encoding='utf-8').read()
      and 'v3.14' in open(os.path.join(ROOT, 'tests', 'test_cases.py'), encoding='utf-8').read())

# ⑧ 取数口径 KV 首列：模板 / 适配层双写一致，禁止 nowrap 把长标签压进值列
_adapt = open(os.path.join(ROOT, 'assets', 'dynamic-adapt.css'), encoding='utf-8').read()


def _prov_k_rules(src):
    return [x for x in re.findall(r'\.prov-k\{[^}]*\}', src, re.S) if 'white-space' in x]


_tk, _ak = _prov_k_rules(_tpl_src), _prov_k_rules(_adapt)
check("KV 首列 176px：模板与适配层同步",
      'grid-template-columns:176px minmax(0,1fr)' in _tpl_src
      and 'grid-template-columns:176px minmax(0,1fr)' in _adapt)
check("KV 标签不再 nowrap，长标签可换行",
      bool(_tk) and bool(_ak)
      and all('white-space:nowrap' not in x and 'white-space:normal' in x for x in _tk + _ak)
      and any('overflow-wrap:anywhere' in x for x in _tk + _ak)
      and any('min-width:0' in x for x in _tk + _ak))

# ============ T23【v3.14】实战复盘：入参 / 门禁 / 版式全量负向回归 ============
print("\n【T23】v3.14：NaN·Inf / 交叉验证 / 占位符 / 出处 / 门禁加固 / 模板版式")
import math as _math, tempfile as _tf


def _blocked(spec, kw):
    try:
        generate(dict(ok_params, **spec), TPL)
        return False
    except SystemExit as e:
        return kw in str(e)


check("v3.14 阻断入参 NaN", _blocked({'fy3': float('nan')}, '有限数'))
check("v3.14 阻断入参 Infinity", _blocked({'current_price': float('inf')}, '有限数'))
check("v3.14 阻断 rf_rate NaN", _blocked({'rf_rate': float('nan')}, '有限数'))
check("v3.14 确定性三项字符串友好阻断", _blocked({'policy_score': '1'}, '必须为数字'))
check("v3.14 确定性三项布尔阻断", _blocked({'tech_score': True}, '必须为数字'))
check("v3.14 FY1=0 仍阻断", _blocked({'fy1': 0}, 'FY1'))
_vd_ok = G.validate_params(dict(ok_params))
check("v3.14 合法参数零错误", not _vd_ok, str(_vd_ok[:2]))

check("v3.14 七项明细全空阻断", _blocked({'cross': {'verdict': '✅ 通过'}}, 'CROSS_ROWS'))
_cr3 = dict(ok_params['cross']); _cr3['checks'] = ['✅ 通过'] * 3
check("v3.14 CHK 缺项给出准确提示", _blocked({'cross': _cr3}, 'CHK4'))
_crv = dict(ok_params['cross']); _crv['verdict'] = '<br>'
check("v3.14 纯标签 verdict 阻断", _blocked({'cross': _crv}, '交叉验证结论'))
check("v3.14 参数自带 {{KEY}} 阻断", _blocked({'company': '测试{{EVIL}}公司'}, '残留未替换占位符'))
check("v3.14 payout_basis='x' 阻断",
      _blocked({'payout': 0.71, 'payout_basis': 'x', 'payout_note': ''}, 'P0-P'))
check("v3.14 payout_basis='TODO' 阻断",
      _blocked({'payout': 0.71, 'payout_basis': 'TODO', 'payout_note': ''}, 'P0-P'))
check("v3.14 合法依据文本仍放行", not G.payout_basis_audit(
    {'payout': 0.71, 'payout_basis': '近5年分红实施公告回算'})[0])
_noid = [dict(s) for s in ok_params['sources']]
for _s in _noid:
    _s.pop('id', None)
check("v3.14 省略 id 自动补号不误杀", not [e for e in G.source_refs_audit(
    dict(ok_params, sources=_noid))[0] if '编号格式非法' in e])
_badtier = [dict(s) for s in ok_params['sources']]
_badtier[0]['tier'] = '伪造'
check("v3.14 tier 白名单阻断", any('tier 分级非法' in e for e in G.source_refs_audit(
    dict(ok_params, sources=_badtier))[0]))
_broken_tpl = _tpl_src.replace('<!-- MARKET:A股 -->', '<!-- MARKET:A 股 -->', 1)
with _tf.NamedTemporaryFile('w', suffix='.html', encoding='utf-8', delete=False) as _tfh:
    _tfh.write(_broken_tpl); _bad_tpl_path = _tfh.name
try:
    generate(dict(ok_params), _bad_tpl_path)
    check("v3.14 市场块注释错配阻断", False, '未阻断')
except SystemExit as _e:
    check("v3.14 市场块注释错配阻断", '市场块注释' in str(_e))
os.unlink(_bad_tpl_path)

_e_srcset = G.validate_hydro(h20.replace('</body>', '<img srcset="https://cdn/x.png"></body>'))[0]
check("v3.14 P0-E 拦 srcset 远程资源", any('P0-E' in e for e in _e_srcset))
_e_proto = G.validate_hydro(h20.replace('</body>', '<img src="//cdn/x.png"></body>'))[0]
check("v3.14 P0-E 拦协议相对资源", any('P0-E' in e for e in _e_proto))
_e_noquote = G.validate_hydro(h20.replace('</body>', '<img src=https://cdn/x.png></body>'))[0]
check("v3.14 P0-E 拦无引号远程资源", any('P0-E' in e for e in _e_noquote))
_e_href = G.validate_hydro(h20.replace('</body>', '<a href=https://evil.com>x</a></body>'))[0]
check("v3.14 P0-H 拦无引号外链", any('P0-H' in e for e in _e_href))
_e_upper = G.validate_hydro(h20.replace('</body>', '<A HREF="https://evil.com">x</A></body>'))[0]
check("v3.14 P0-H 拦大写 A 标签", any('P0-H' in e for e in _e_upper))
_e_id = G.validate_hydro(h20.replace('id="o-grade"', 'id="o-grade-X"', 1))[0]
check("v3.14 P0-F 全量 id 比对生效", any('P0-F' in e and 'o-grade' in e for e in _e_id))
check("v3.14 P0-F 正常报告零误报", not [e for e in G.validate_hydro(h20)[0] if 'P0-F' in e])

check("v3.14 consensus cells 字符串不拆字",
      '>12.3' in G.render_consensus_tbl({'platforms': [{'name': 'X', 'cells': ['12.3（5家）']}]}))
check("v3.14 calib chips 字符串整标签", '超高' in G.render_calib({'chips': ['超高']}))
check("v3.14 prov_rows 非二元组跳过",
      G.render_prov_rows([('A', 'x'), 'bad']) ==
      '<div class="prov-row"><div class="prov-k">A</div><div class="prov-v">x</div></div>')
_orig_load_brand = G.load_brand
G.load_brand = lambda: dict(_orig_load_brand(), wechat='A&B <script>x</script>')
try:
    _bf = G.render_brand_footer()
finally:
    G.load_brand = _orig_load_brand
check("v3.14 brand 页脚转义 & 与标签",
      'A&amp;B' in _bf and '<script>x</script>' not in _bf)

# 模板 / 适配层静态契约（浏览器量测见 /tmp 验收脚本，这里锁死最小实现）
check("v3.14 测算器排在目录卡与数据底座之前",
      _tpl_src.index('<!-- ============ 🧮 手动测算器')
      < _tpl_src.index('<!-- ============ 报告导航 TOC')
      < _tpl_src.index('<!-- ============ 数据底座卡'))
check("v3.14 适配层删掉全局渐入强制可见",
      '.step.reveal-init,.viz.reveal-init{opacity:1 !important;transform:none !important;}' not in
      _adapt.split('@media print{')[0])
check("v3.14 1200px 断点统一为 1199px",
      '@media(max-width:1199px){.side-nav{display:none;}}' in _adapt
      and '@media(max-width:1200px)' not in _adapt)
check("v3.14 深色打印亮色反相在位",
      'html[data-theme="dark"]{' in _adapt.split('@media print{')[1]
      and '--p-paper:#fff' in _adapt.split('@media print{')[1])
check("v3.14 测算器重置用 ORIG_RAW", "const ORIG_RAW=Object.assign({},ORIG);" in _tpl_src
      and "for(const k in ORIG_RAW)" in _tpl_src)
check("v3.14 v-grade 操作内层 span", "const vg=$('v-grade').firstElementChild" in _tpl_src)
check("v3.14 渐入 IO 未派发兜底在位",
      'var fired=false;' in _tpl_src and 'if(!fired) items.forEach' in _tpl_src)
check("v3.14 classic 深色补 --blue-deep", '--blue-deep:#5b9bf0;' in _tpl_src)
check("v3.14 生成器有限数 / 弱依据 / 全量 id 门禁在位",
      'math.isfinite' in open(os.path.join(ROOT, 'scripts', 'generate_report.py'),
                              encoding='utf-8').read()
      and '_WEAK_BASIS' in open(os.path.join(ROOT, 'scripts', 'generate_report.py'),
                                encoding='utf-8').read()
      and 'P0-F JS 引用了不存在的 id' in open(os.path.join(ROOT, 'scripts',
                                                        'generate_report.py'), encoding='utf-8').read())

# ============ T24 v3.14 收尾：打印可读性 / 死 CSS / 资产门禁 ============
print("\n【T24】v3.14 打印态 Hero 可读性修复 + 样式资产硬失败")
_skin = open(os.path.join(ROOT, 'assets', 'hydro-skin.css'), encoding='utf-8').read()
_print_adapt = _adapt.split('@media print{', 1)[1]
_print_skin = _skin.split('/* ⑮ ── 打印', 1)[1].split('@media print{', 1)[1]

# 模板里带 !important 的浅色文字，换肤后必须被更高优先级的打印规则覆盖
for _sel in ('header#sec-overview h1', 'header#sec-overview .sub',
             'header#sec-overview .hero-kpi .hk-v',
             'header#sec-overview .hero-kpi .hk-k',
             'header#sec-overview .hero-kpi .hk-s',
             'header#sec-overview .hero-meta .hm'):
    check("v3.14 适配层打印覆盖 %s" % _sel,
          ('html body %s' % _sel) in _print_adapt)
check("v3.14 打印覆盖用 !important 且压成墨色",
      'color:#1a1a2e !important' in _print_adapt
      and 'html body header#sec-overview{' in _print_adapt
      and 'background:#fff !important' in _print_adapt)
check("v3.14 打印顺带修 th / .step h3 / .cp-head 白字",
      all(('html body %s' % s) in _print_adapt
          for s in ('th', '.step h3', '.calc-panel .cp-head')))
check("v3.14 hydro-skin 打印段改用真实 DOM",
      'html body header#sec-overview h1' in _print_skin
      and 'html body .step-card' not in _print_skin
      and 'html body .hero h1' not in _print_skin
      and '.subtitle' not in _print_skin
      and '.mt-v' not in _print_skin)

# P0-A 门禁：空 style / 缺打印覆盖都要阻断
_e_empty_style = G.validate_hydro(
    re.sub(r'(<style id="hydro-skin"[^>]*>).*?(</style>)', r'\1\2', h20, count=1, flags=re.S))[0]
check("v3.14 P0-A 拦空 <style> 假通过",
      any('内联 CSS 内容异常过短' in e for e in _e_empty_style))
_e_no_print = G.validate_hydro(h20.replace('html body header#sec-overview h1,',
                                           'html body header#sec-overview H1_REMOVED,'))[0]
check("v3.14 P0-A 拦打印 Hero 覆盖丢失",
      any('缺打印态 Hero 文字颜色覆盖' in e for e in _e_no_print))
check("v3.14 P0-A 正常报告零误报",
      not [e for e in G.validate_hydro(h20)[0] if 'P0-A' in e])

# _asset()：空 CSS / 缺文件必须硬失败，不能静默注入空 <style>
_orig_assets = G.ASSETS
with tempfile.TemporaryDirectory() as _td:
    open(os.path.join(_td, 'empty.css'), 'w', encoding='utf-8').write('   \n')
    open(os.path.join(_td, 'ok.css'), 'w', encoding='utf-8').write('/* ok */\n' + 'a{color:red}\n' * 40)
    G.ASSETS = _td
    try:
        _msg_missing = ''
        try:
            G._asset('nope.css')
        except SystemExit as e:
            _msg_missing = str(e)
        _msg_empty = ''
        try:
            G._asset('empty.css')
        except SystemExit as e:
            _msg_empty = str(e)
        check("v3.14 _asset 缺文件硬失败", '缺少样式资产' in _msg_missing)
        check("v3.14 _asset 空 CSS 硬失败", '异常过短' in _msg_empty)
        check("v3.14 _asset 正常 CSS 放行", len(G._asset('ok.css')) > 200)
    finally:
        G.ASSETS = _orig_assets

# ============ T25 v3.14 打印对比度扫描补修（浏览器实测回归） ============
print("\n【T25】v3.14 全 DOM 对比度：亮/暗 × 屏幕/打印四态")


def _wc(fg, bg):
    def _l(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        f = lambda v: v / 12.92 if v <= 0.03928 else ((v + .055) / 1.055) ** 2.4
        r, g, b = map(f, c)
        return .2126 * r + .7152 * g + .0722 * b
    a, b = sorted((_l(fg), _l(bg)), reverse=True)
    return (a + .05) / (b + .05)


check("v3.14 次级文字令牌已压到 #646c71", '--p-ink-3:#646c71;' in _skin)
for _bg, _min in (('#f3f0e8', 4.5), ('#faf8f3', 4.5), ('#ffffff', 4.5)):
    check("v3.14 次级文字在 %s 上 ≥4.5:1（实得 %.2f）"
          % (_bg, _wc('#646c71', _bg)), _wc('#646c71', _bg) >= _min)
check("v3.14 旧 #8b9295 对比度确实不达标", _wc('#8b9295', '#f3f0e8') < 3.0)
check("v3.14 打印态 --p-ink-3 再压深", 'html:root{--p-ink-3:#5a6178;}' in _print_adapt)
for _sel in ('.calib', '.calib-chip', '.xv-k', 'code'):
    check("v3.14 深色打印硬编码深底已改亮底 %s" % _sel,
          ('html[data-theme="dark"] body %s' % _sel) in _print_adapt)
for _sel in ('.meta-card-title', '.prov-tbl td:first-child', '.calib-head'):
    check("v3.14 深色打印硬编码浅字已压墨 %s" % _sel,
          ('html[data-theme="dark"] body %s' % _sel) in _print_adapt)
_sync_sh = open(os.path.join(ROOT, 'sync.sh'), encoding='utf-8').read()
_sync_cmds = '\n'.join(l for l in _sync_sh.splitlines()
                       if l.strip() and not l.lstrip().startswith('#'))
check("v3.14 sync.sh 不再用 SSH 版 gh repo clone",
      'gh repo clone' not in _sync_cmds and 'gh auth token' in _sync_sh
      and 'x-access-token' in _sync_sh
      and 'git clone --quiet "$AUTH_URL"' in _sync_cmds)
check("v3.14 sync.sh 校验发布包 CSS 内容与打印覆盖",
      '内容异常过短' in _sync_sh and '打印态 Hero 文字覆盖' in _sync_sh)

# ============ 汇总 ============
print("\n" + "=" * 56)
print(f"  测试结果：{passed} 通过 / {failed} 失败")
print("=" * 56)
sys.exit(1 if failed else 0)
