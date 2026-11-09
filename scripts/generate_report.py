#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""动态估值法 v3.14 —— 报告生成器 + 校验器

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
import re, json, sys, os, base64, math
from html import escape as _esc
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, 'assets')


# ---------- v3.13 富文本净化：只放行可核查链接与基础排版标签 ----------
# 背景：params 的判断链 / 取数口径 / 一致预期平台名里**故意**带
#   <a class="ref" href="https://..." target="_blank" rel="noopener">[S09]</a>
# 这类正文出处上标。早期直接拼进 HTML：取数文本一旦含裸 < / & / 未转义标签
# 就会破坏表格甚至注入脚本。整体 escape 又会把合法的 [S##] 链接变成字面量。
# 因此改为白名单净化：文本节点一律转义；标签/属性按白名单重建；
# <a> 只接受 http(s) 并自动补齐 target/rel（顺带兜住 P0-H 门禁）。
_RICH_TAGS = ('a', 'b', 'strong', 'em', 'i', 'u', 'sup', 'sub', 'br', 'span', 'code')
_RICH_ATTRS = {
    'a': ('href', 'class', 'title'),
    'span': ('class', 'title'),
    'sup': ('class',), 'sub': ('class',), 'code': ('class',),
}
_RICH_VOID = ('br',)
_SAFE_HREF = re.compile(r'^https?://\S+$', re.I)


class _RichSanitizer(HTMLParser):
    """把 params 文本转成安全 HTML 片段；幂等（可对已净化文本重复调用）。"""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts = []
        self.stack = []          # [(原标签, 实际输出标签 or None)]

    def handle_data(self, data):
        self.parts.append(_esc(data, quote=True))

    def handle_entityref(self, name):
        self.parts.append('&%s;' % name)

    def handle_charref(self, name):
        self.parts.append('&#%s;' % name)

    def handle_comment(self, data):
        pass                      # 注释一律丢弃，避免 <!-- --> 破坏结构

    def handle_decl(self, decl):
        pass

    def handle_starttag(self, tag, attrs):
        self._open(tag, attrs, tag in _RICH_TAGS)

    def handle_startendtag(self, tag, attrs):
        emit = self._open(tag, attrs, tag in _RICH_TAGS)
        if (emit and emit not in _RICH_VOID
                and self.stack and self.stack[-1][0] == tag):
            self.stack.pop()
            self.parts.append('</%s>' % emit)

    def _open(self, tag, attrs, allowed):
        if not allowed:
            self.parts.append('&lt;%s&gt;' % tag)
            self.stack.append((tag, None))
            return None
        d = dict(attrs)
        emit = tag
        if tag == 'a' and not _SAFE_HREF.match(str(d.get('href') or '')):
            emit = 'span'         # 非 http(s) / 空链接：降级为纯文本容器
        out = []
        for k in _RICH_ATTRS.get(tag, ()):
            v = d.get(k)
            if v is None or (k == 'href' and emit != 'a'):
                continue
            out.append(' %s="%s"' % (k, _esc(str(v), quote=True)))
        if emit == 'a':
            out.append(' target="_blank"')
            out.append(' rel="noopener noreferrer"')
        self.parts.append('<%s%s>' % (emit, ''.join(out)))
        if emit not in _RICH_VOID:
            self.stack.append((tag, emit))
        return emit

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1][0] == tag:
            _, emit = self.stack.pop()
            if emit and emit not in _RICH_VOID:
                self.parts.append('</%s>' % emit)
            elif not emit:
                self.parts.append('&lt;/%s&gt;' % tag)
        else:
            self.parts.append('&lt;/%s&gt;' % tag)

    def result(self):
        for orig, emit in reversed(self.stack):
            if emit and emit not in _RICH_VOID:
                self.parts.append('</%s>' % emit)
        return ''.join(self.parts)


def sanitize_rich(text):
    """params 文本 → 安全 HTML 片段。非字符串按 str 处理，None → 空串。"""
    if text is None:
        return ''
    s = str(text)
    if not s:
        return ''
    p = _RichSanitizer()
    p.feed(s)
    p.close()
    return p.result()


_T = sanitize_rich  # 渲染边界的简写

# ---------- HYDRO PAPER v3.0 皮肤（v3.9：对齐八维 / 七步设计系统） ----------
# 三层 CSS：① 模板内置 <style> → ② hydro-skin.css → ③ dynamic-adapt.css。
# 全部整段内联进 HTML：报告单独转发不依赖同目录 assets/（七步 v2.1.2 事故）。
HYDRO_MARK = 'data-design="HYDRO PAPER v3.0"'


def _asset(name):
    """v3.14：缺文件 / 空 CSS 必须硬失败。

    旧版只做 open().read()：文件被清空时仍会注入 <style id="hydro-skin"></style>，
    P0-A 只查 id 字符串就会假通过，报告静默退回 classic 皮肤、连打印可读性一起丢。
    """
    path = os.path.join(ASSETS, name)
    if not os.path.isfile(path):
        raise SystemExit('❌ 缺少样式资产：%s' % path)
    with open(path, encoding='utf-8') as f:
        css = f.read()
    if len(css.strip()) < 200:
        raise SystemExit('❌ 样式资产内容异常过短（%d 字符）：%s' % (len(css.strip()), path))
    return css


def load_brand():
    with open(os.path.join(ASSETS, 'brand.json'), encoding='utf-8') as f:
        return json.load(f)


def qr_data_uri(kind, brand):
    """二维码一律 base64 内联；brand.json 的 qr_*_data 为主，PNG 为兜底。"""
    key = 'qr_%s_data' % kind
    if brand.get(key):
        return 'data:image/png;base64,' + brand[key]
    fn = brand.get('qr_%s' % kind)
    with open(os.path.join(ASSETS, fn), 'rb') as f:
        return 'data:image/png;base64,' + base64.b64encode(f.read()).decode('ascii')


def hero_art_svg():
    """装饰性等高线，与八维 / 七步 Hero 同款语言；不表示任何真实数据曲线。"""
    return (
        '<svg class="hero-art" viewBox="0 0 780 420" preserveAspectRatio="xMidYMid slice" '
        'aria-hidden="true"><title>装饰性等高线示意，不代表数据</title>'
        '<path class="contour" d="M-40 330 C120 260 210 380 360 300 S600 210 820 250"/>'
        '<path class="contour strong" d="M-40 290 C120 220 220 340 370 260 S610 170 820 210"/>'
        '<path class="contour" d="M-40 250 C130 180 230 300 380 220 S620 130 820 170"/>'
        '<path class="contour" d="M-40 210 C140 140 240 260 390 180 S630 90 820 130"/>'
        '<path class="contour" d="M-40 170 C150 100 250 220 400 140 S640 50 820 90"/>'
        '<path class="river" d="M60 420 C160 300 260 350 340 220 S520 120 700 60"/>'
        '<circle class="ring" cx="560" cy="196" r="92"/>'
        '<circle class="ring" cx="560" cy="196" r="58"/>'
        '<circle class="ring" cx="560" cy="196" r="24"/>'
        '<text class="art-label" x="500" y="340">DYNAMIC VALUATION / HYDRO</text>'
        '</svg>')


def inline_css_layers():
    return ('\n<style id="hydro-skin" %s>\n%s\n</style>\n'
            '<style id="dynamic-adapt" data-design="HYDRO PAPER v3.0 · dynamic-valuation">\n%s\n</style>\n'
            % (HYDRO_MARK, _asset('hydro-skin.css'), _asset('dynamic-adapt.css')))


def render_brand_footer():
    """紧凑溯源条：公众号 / 视频号各一行，二维码点击放大。零远程链接。"""
    b = load_brand()
    # v3.14：brand.json 的值一律转义，避免名称 / note 里的 & 或标签注入页脚。
    hint = _esc(str(b.get('hint', '点击二维码查看大图')))
    rows = []
    for kind, raw_name, key in (('公众号', b.get('wechat', ''), 'wechat'),
                                ('视频号', b.get('video', ''), 'video')):
        name = _esc(str(raw_name))
        uri = _esc(qr_data_uri(key, b), quote=True)
        rows.append(
            '<div class="brand-channel">'
            '<div class="brand-channel-copy">'
            '<p class="brand-channel-name">%s「%s」</p>'
            '<p class="brand-channel-hint">%s</p>'
            '</div>'
            '<button class="brand-qr" type="button" aria-label="放大%s「%s」二维码">'
            '<img src="%s" alt="%s「%s」二维码">'
            '</button></div>' % (kind, name, hint, kind, name, uri, kind, name))
    return '\n'.join([
        '<footer class="report-footer">',
        '<div class="brand-block">',
        '<p class="brand-head"><span class="brand-kicker">%s</span>'
        '<span class="brand-title">%s</span></p>' % (_esc(str(b.get('kicker', '原创出品 · 欢迎溯源'))),
                                                   _esc(str(b.get('title', '')))),
        '<div class="brand-channels">', '\n'.join(rows), '</div>',
        '<p class="brand-meta">',
        '<span class="brand-note">%s</span>' % _esc(str(b.get('note', ''))),
        '<span class="brand-note">本报告只做估值拆解，不构成投资建议</span>',
        '<span class="report-attribution">分析框架：%s ｜ 方法论：梁宇峰《估值的力量》第三章</span>'
        % _esc(str(b.get('method', '动态估值法 · 松幽'))),
        '</p></div>',
        '<div class="qr-lightbox" id="qrLightbox" hidden><img alt="二维码大图"></div>',
        '</footer>',
    ])


BRAND_JS = """<script>
(function(){
  var box=document.getElementById('qrLightbox'); if(!box) return;
  var big=box.querySelector('img');
  var btns=document.querySelectorAll('.brand-qr');
  for(var i=0;i<btns.length;i++){
    (function(btn){btn.addEventListener('click',function(){
      var im=btn.querySelector('img'); if(im){big.src=im.src;box.hidden=false;}
    });})(btns[i]);
  }
  box.addEventListener('click',function(){box.hidden=true;});
  document.addEventListener('keydown',function(e){if(e.key==='Escape')box.hidden=true;});
})();
</script>
"""


def apply_skin(html):
    """注入三层 CSS / Hero 装饰 / brand 页脚。不动测算器 id、SVG 坐标、市场块。

    v3.13：用 rfind 而非首个 `</head>`。模板 CSS 注释里一旦出现字面 `</head>`
    （v3.10 截图注入踩过），首个匹配会把两层 CSS 插进注释中间导致样式失效。
    """
    i = html.rfind('</head>')
    if i < 0:
        raise SystemExit('❌ 模板缺少 </head>，无法注入 HYDRO 三层 CSS')
    html = html[:i] + inline_css_layers() + html[i:]
    html = html.replace('<header id="sec-overview">',
                        '<header id="sec-overview">' + hero_art_svg(), 1)
    # 旧单行署名 footer 移除；brand 页脚作为 <body> 直接子元素插在 </body> 前，
    # 与 .wrap 兄弟，避免 padding-left 叠加造成页脚右偏（七步 v2.1.0 事故）。
    # v3.14：旧署名 footer 带属性时也要删，否则与新 brand 页脚共存。
    html = re.sub(r'<footer\b[^>]*>.*?</footer>', '', html, count=1, flags=re.S)
    html = html.replace('</body>', render_brand_footer() + '\n' + BRAND_JS + '</body>', 1)
    return html


class _FooterDepth(HTMLParser):
    """栈解析：确认 footer.report-footer 不嵌在 .wrap 内。"""

    VOID = ('br', 'img', 'input', 'meta', 'link', 'hr', 'source', 'path', 'circle', 'text')

    def __init__(self):
        super().__init__()
        self.stack = []
        self.seen = False
        self.inside_wrap = False

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        cls = (d.get('class') or '').split()
        if tag == 'footer' and 'report-footer' in cls:
            self.seen = True
            self.inside_wrap = any(t == 'div' and 'wrap' in (c or '').split()
                                   for t, c in self.stack)
        if tag not in self.VOID:
            self.stack.append((tag, d.get('class') or ''))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break


def validate_hydro(html):
    """P0-A~P0-H 门禁：不通过则阻断输出。返回 (errors, warnings)。

    v3.10：P0-E 收窄为「远程资源」（src/link/@import/url()），放行 <a href="https://...">
    这类可核查出处链接；P0-G 侧栏首屏隐藏；P0-H 出处章节与链接安全。
    """
    errs, warns = [], []
    for mark in ('id="hydro-skin"', 'id="dynamic-adapt"', HYDRO_MARK):
        if mark not in html:
            errs.append('P0-A 三层 CSS 缺失：%s' % mark)
    # v3.14：只查 id 字符串会放过「空 <style>」——换肤失效但门禁全绿。
    for sid in ('hydro-skin', 'dynamic-adapt'):
        m = re.search(r'<style id="%s"[^>]*>(.*?)</style>' % sid, html, re.S)
        if m and len(m.group(1).strip()) < 200:
            errs.append('P0-A %s 内联 CSS 内容异常过短，换肤可能未生效' % sid)
    # v3.14：打印态 Hero 文字覆盖。模板 @media print 的 !important 浅色文字
    # 在 HYDRO 白纸底上不可读，且 hydro-skin 旧 .hero 选择器无对应 DOM。
    for mark in ('html body header#sec-overview h1',
                 'html body header#sec-overview .sub',
                 'html body header#sec-overview .hero-kpi .hk-v'):
        if mark not in html:
            errs.append('P0-A 缺打印态 Hero 文字颜色覆盖：%s' % mark)
    # v3.13：CSS 必须落在 <head> 内。注释里的字面 </head> 曾让注入点错位。
    skin_at, body_at = html.find('id="hydro-skin"'), html.find('<body')
    if skin_at >= 0 and body_at >= 0 and skin_at > body_at:
        errs.append('P0-A 三层 CSS 被注入到 <body> 之后，</head> 定位错误')
    p = _FooterDepth()
    p.feed(html)
    if not p.seen:
        errs.append('P0-B brand 页脚缺失')
    elif p.inside_wrap:
        errs.append('P0-C footer.report-footer 嵌在 .wrap 内，页脚会双重左偏移')
    if html.count('data:image/png;base64,') < 2:
        errs.append('P0-D 两张二维码未内嵌')
    # P0-E：只禁远程「资源」。出处链接是 <a>，不加载外部资源，不算破坏自包含。
    # v3.14：补 srcset / poster / data、无引号属性、协议相对 //、大写标签。
    _REMOTE = r'(?:https?:)?//'
    if (re.search(r'<(?:img|script|iframe|video|audio|source|object|embed|track)\b[^>]*'
                  r'\b(?:src|srcset|poster|data)\s*=\s*["\']?\s*' + _REMOTE,
                  html, re.I)
            or re.search(r'<link\b[^>]*\bhref\s*=\s*["\']?\s*' + _REMOTE, html, re.I)
            or re.search(r'@import\s+[^;]*' + _REMOTE, html, re.I)
            or re.search(r'url\(\s*["\']?\s*' + _REMOTE, html, re.I)):
        errs.append('P0-E 存在远程资源引用，违反单文件自包含')
    if re.search(r'(?:src|href)\s*=\s*["\']assets/', html):
        errs.append('P0-E 存在同目录 assets/ 相对引用，单文件不自包含')
    # 出处链接安全：http(s) 必须新窗口 + noopener；禁 javascript:/data:/空 # 链接。
    for m in re.finditer(r'<a\b[^>]*>', html, re.I):
        tag = m.group(0)
        # v3.14：引号可选 + 大小写不敏感，无引号 href 不再整条跳过。
        hm = re.search(r'\bhref\s*=\s*["\']?([^"\'>\s]*)', tag, re.I)
        if not hm:
            continue
        href = hm.group(1)
        if href.startswith(('javascript:', 'data:', 'vbscript:')):
            errs.append('P0-H 出处链接协议非法：%s' % href[:40])
        elif href.startswith('http'):
            if 'target="_blank"' not in tag and "target='_blank'" not in tag:
                errs.append('P0-H 外部出处链接缺 target="_blank"：%s' % href[:60])
            if 'noopener' not in tag:
                errs.append('P0-H 外部出处链接缺 rel=noopener：%s' % href[:60])
        elif href == '#':
            errs.append('P0-H 存在 href="#" 空链接，不得冒充出处')
    for i in ('h-price', 'v-fy3eff', 'btn-apply', 'qrLightbox'):
        if ('id="%s"' % i) not in html:
            errs.append('P0-F 测算器 / 灯箱 id 丢失：%s' % i)
    # v3.14：只锁 4 个 id 时，模板漏改任意一个 $('...') 目标都会让整个 IIFE
    # 抛 TypeError，折叠 / 应用 / 重置全部失效，而门禁全绿。改为全量比对。
    used_ids = set(re.findall(r"\$\('([A-Za-z0-9_-]+)'\)", html)) | set(
        re.findall(r"getElementById\(['\"]([A-Za-z0-9_-]+)['\"]\)", html))
    defined_ids = set(re.findall(r'\bid="([A-Za-z0-9_-]+)"', html))
    missing_ids = sorted(used_ids - defined_ids)
    if missing_ids:
        errs.append('P0-F JS 引用了不存在的 id：%s' % '、'.join(missing_ids[:10]))
    if '以上内容基于公开数据和量化分析' not in html:
        errs.append('P0-F 固定免责声明缺失')
    # P0-G：侧栏初始隐藏 + 滚动显隐 JS，对齐八维 / 七步。
    if 'class="side-nav nav-hidden"' not in html:
        errs.append('P0-G 侧栏缺初始 nav-hidden，首屏会常驻')
    if "classList.toggle('nav-hidden'" not in html:
        errs.append('P0-G 侧栏滚动显隐 JS 缺失')
    # P0-H：数据来源汇总章节与卡片列表（v3.11 起为卡片式，禁止退回窄列表格）。
    if 'id="sec-sources"' not in html:
        errs.append('P0-H 数据来源汇总章节缺失')
    for mark in ('class="src-list"', 'class="src-card"', 'class="src-card-fields"'):
        if mark not in html:
            errs.append('P0-H 出处卡片列表缺失：%s' % mark)
    if 'class="src-tbl"' in html:
        errs.append('P0-H 检出旧窄列 .src-tbl，出处长文本会被拆行')
    if 'href="https://' not in html and '转引 · 未取得原文链接' not in html:
        errs.append('P0-H 出处列表既无可核查链接也无转引标注')
    if '{{' in html:
        warns.append('皮肤注入后仍检出 {{ 字符，请复核占位符')
    return errs, warns


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


def _raw(x, n=6):
    """全精度字符串（去尾零）：测算器 input value 专用，避免 2 位展示值回填造成偏差。"""
    s = f"{float(x):.{n}f}"
    return s.rstrip('0').rstrip('.') if '.' in s else s


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
    if '⚠️' in t or '警告' in t or '存疑' in t or '预警' in t:    return 'warn'
    if '✅' in t or '通过' in t:                    return 'ok'
    return ''


def wrap_sem(text):
    """给校验项文本套语义色 span。已自带 span 的原样返回（防双重包裹）。"""
    t = str(text or '').strip()
    if not t:
        return ''
    if t.startswith('<span'):
        return _T(t)
    c = sem_class(t)
    return f'<span class="{c}">{_T(t)}</span>' if c else _T(t)


# ---------- 排版渲染（长文本结构化：禁止糊成一坨） ----------
def _split_clauses(t):
    """按「；」拆分（只在括号外拆）；拆出的片段过长时再按「。」拆成句。"""
    out, buf, depth = [], '', 0
    for ch in t:
        if ch in '（(':
            depth += 1
        elif ch in '）)' and depth > 0:
            depth -= 1
        if ch in '；;' and depth == 0:
            out.append(buf)
            buf = ''
        else:
            buf += ch
    out.append(buf)
    parts = []
    for seg in (x.strip() for x in out if x.strip()):
        if len(seg) <= 80:
            parts.append(seg)
            continue
        buf, depth = '', 0
        for ch in seg:
            if ch in '（(':
                depth += 1
            elif ch in '）)' and depth > 0:
                depth -= 1
            buf += ch
            if ch == '。' and depth == 0:
                parts.append(buf.strip())
                buf = ''
        if buf.strip():
            parts.append(buf.strip())
    return [p for p in parts if p]


def render_clauses(text):
    """长句按「；」拆成带圆点的分行段；始终包一层 .clauses，保证各行左边缘对齐。"""
    t = str(text or '').strip()
    if not t:
        return ''
    parts = _split_clauses(t)
    return '<span class="clauses">' + ''.join(f'<span class="seg">{_T(x)}</span>' for x in parts) + '</span>'


def _num_cell(v, n=None):
    if v is None or v == '':
        return ''
    try:
        s = f2(float(v))
    except (TypeError, ValueError):
        s = _T(v)
    if n:
        s += f'<span class="n">{n} 家</span>'
    return s


def render_consensus_tbl(c):
    """结构化一致预期（平台 × 年份）→ 小表。缺结构时返回空串走回退分支。"""
    if not c or not c.get('platforms'):
        return ''
    years = c.get('years') or ['2026E', '2027E', '2028E']
    head = '<tr><th>一致预期平台</th>' + ''.join(f'<th>{_T(y)}</th>' for y in years) + '</tr>'
    body = ''
    # v3.14：cells 元素期望 [值, 家数]；传字符串时旧版会把字符拆成值和家数。
    def _cell_td(x):
        if isinstance(x, (list, tuple)):
            return '<td>%s</td>' % _num_cell(x[0], x[1] if len(x) > 1 else None)
        return '<td>%s</td>' % _num_cell(x)
    for pl in c['platforms']:
        cells = ''.join(_cell_td(x) for x in pl.get('cells', []))
        body += f'<tr><td>{_T(pl.get("name", ""))}</td>{cells}</tr>'
    ad = c.get('adopted')
    if ad:
        cells = ''.join('<td>%s</td>' % _num_cell(x[0] if isinstance(x, (list, tuple)) else x) for x in ad)
        body += f'<tr class="adopted"><td>{_T(c.get("adopted_label", "采用值 · 双平台均值"))}</td>{cells}</tr>'
    return f'<table class="prov-tbl"><thead>{head}</thead><tbody>{body}</tbody></table>'


def render_prov_rows(rows):
    out = ''
    for item in rows or []:
        # v3.14：非二元组直接跳过，不再抛 unpack 异常。
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        k, v = item[0], item[1]
        if not str(v or '').strip():
            continue
        out += f'<div class="prov-row"><div class="prov-k">{_T(k)}</div><div class="prov-v">{_T(v)}</div></div>'
    return out


SRC_TIER_CLASS = {'一手': 'a', '接口': 'b', '研报转载': 'c', '媒体': 'c',
                  '转引': 'x', '未采用': 'x'}


def render_sources(rows):
    """v3.11 数据来源汇总·卡片式：每条出处一张卡，长文本全宽，不再被窄列拆行。

    row: {id,use,name,value,caliber,tier,url,date}。
    url 为空 → 显示「转引 · 未取得原文链接」，禁止编造 URL 冒充一手核实。
    """
    if not rows:
        return '<p class="src-note">本轮未提供 sources 条目。</p>'
    cards = []
    for i, s in enumerate(rows, 1):
        sid = _esc(str(s.get('id') or 'S%02d' % i))
        tier = str(s.get('tier') or '').strip()
        tcls = SRC_TIER_CLASS.get(tier, 'x')
        tier_html = '<span class="src-tag %s">%s</span>' % (tcls, _esc(tier)) if tier else ''
        url = str(s.get('url') or '').strip()
        if url:
            link = ('<a class="src-a" href="%s" target="_blank" rel="noopener noreferrer">'
                    '核查链接 ↗</a>' % _esc(url, quote=True))
        else:
            link = '<span class="src-tag x src-no-url">转引 · 未取得原文链接</span>'
        cards.append(
            '<article class="src-card">'
            '<div class="src-card-top">'
            '<span class="src-id">%s</span><span class="src-card-use">%s</span>%s'
            '<span class="src-card-meta"><span class="src-card-date">%s</span>'
            '<span class="src-link">%s</span></span>'
            '</div>'
            '<p class="src-card-name">%s</p>'
            '<dl class="src-card-fields">'
            '<div class="src-card-row"><dt>本报告引用</dt><dd>%s</dd></div>'
            '<div class="src-card-row"><dt>口径</dt><dd>%s</dd></div>'
            '</dl></article>'
            % (sid, _esc(str(s.get('use') or '')), tier_html,
               _esc(str(s.get('date') or '')), link,
               _esc(str(s.get('name') or '')), _esc(str(s.get('value') or '')),
               _esc(str(s.get('caliber') or ''))))
    return '<div class="src-list">' + ''.join(cards) + '</div>'


def render_calib(spec):
    """系数校准说明块：title + (chips | items) + note。值位只放值，解释进这里。"""
    if not spec:
        return ''
    h = '<div class="calib">'
    if spec.get('title'):
        h += f'<div class="calib-head">{_T(spec["title"])}</div>'
    chips = spec.get('chips') or []
    if chips:
        # v3.14：chips 元素期望 [label, current]；字符串按整个标签渲染。
        h += '<div class="calib-chips">' + ''.join(
            '<span class="calib-chip%s">%s</span>' % (
                ' current' if isinstance(c, (list, tuple)) and len(c) > 1 and c[1] else '',
                _T(c[0] if isinstance(c, (list, tuple)) else c))
            for c in chips) + '</div>'
    items = spec.get('items') or []
    if items:
        h += '<div class="calib-grid">' + ''.join(
            f'<div class="calib-item"><span>{_T(k)}</span><b>{_T(v)}</b></div>'
            for k, v in (it for it in items if isinstance(it, (list, tuple)) and len(it) >= 2)) + '</div>'
    if spec.get('note'):
        h += f'<p class="calib-note">{_T(spec["note"])}</p>'
    return h + '</div>'


_VD_RE = re.compile(r'^\s*([✅⚠❌])\ufe0f?\s*(通过|预警|不通过|存疑)?\s*[：:]\s*')
_VD_CLASS = {'✅': 'ok', '⚠': 'warn', '❌': 'bad'}
_VD_LABEL = {'ok': '通过', 'warn': '预警', 'bad': '不通过'}


def split_verdict(text):
    """拆出「✅ 通过：」前缀 → (徽章HTML, 正文)。无前缀返回 ('', 原文)。"""
    t = str(text or '').strip()
    m = _VD_RE.match(t)
    if not m:
        return '', t
    cls = _VD_CLASS.get(m.group(1), '')
    label = m.group(2) or _VD_LABEL.get(cls, '')
    mark = m.group(1) + ('\ufe0f' if cls == 'warn' else '')
    return f'<span class="vd {cls}">{mark} {label}</span>', t[m.end():].strip()


CROSS_ITEMS = [
    ('gross', '毛利率', '近3–5年均值'), ('net', '净利率', '近3–5年均值'),
    ('roe', 'ROE', '近3–5年均值'), ('growth', '利润增速', '近3年 CAGR'),
    ('lever', '资产负债率', '最新财报'), ('peer', '同业 PE / PS', '可比公司区间'),
    ('hist', '自身历史估值带', '近5年 PE-TTM'),
]


def render_cross_rows(cross):
    """交叉验证：左指标 / 右判定卡片，长句按「；」分行。"""
    out = ''
    for key, label, sub in CROSS_ITEMS:
        v = cross.get(key, '')
        if not str(v or '').strip():
            continue
        chip, body = split_verdict(v)
        chip_block = f'<div class="xv-vd">{chip}</div>' if chip else ''
        out += (f'<div class="xv-row"><div class="xv-k">{label}<em>{sub}</em></div>'
                f'<div class="xv-v">{chip_block}{render_clauses(body)}</div></div>')
    return out


def render_verdict(text, cls=''):
    t = str(text or '').strip()
    if not t:
        return ''
    chip, body = split_verdict(t)
    if not chip:
        m = re.match(r'^([^：:]{2,8})[：:]\s*(.+)$', t, re.S)
        if m:
            c = sem_class(t) or cls or 'warn'
            chip = f'<span class="vd {c}">{_T(m.group(1).strip())}</span>'
            body = m.group(2)
        else:
            body = t
    return ('<div class="verdict-box"><div class="vb-head">' + (chip + ' ' if chip else '') +
            '校验结论</div>' + '<div class="clauses" style="font-size:13px;line-height:1.85;">' +
            render_clauses(body) + '</div></div>')


def short_label(text, limit=30):
    """取长文首个短语做 chip 标签（无显式 label 时的兜底）。"""
    s = re.sub(r'<[^>]+>', '', str(text or '')).strip()
    if not s:
        return '—'
    s = re.split(r'[（(，,。；;]', s)[0].strip() or s
    return s if len(s) <= limit else s[:limit] + '…'


# 由渲染函数产出的 HTML 片段：内部已完成净化，不可再整体 escape。
# 其余 ctx 值一律视为「params 文本」走白名单净化，防止裸 < / & / 注入标签破坏排版。
RICH_CTX_KEYS = {
    'CONSENSUS_TBL', 'PROV_ROWS', 'CROSS_ROWS', 'VERDICT_BOX',
    'DISCOUNT_CALIB', 'PAYOUT_CALIB', 'GROWTH_CHAIN', 'COMP_CHAIN',
    'MOAT_CHAIN', 'CERT_CHAIN', 'SOURCES_TBL', 'CAP_FLAG', 'OVER_CAP_BLOCK',
} | {f'CHK{i}' for i in range(1, 8)}


def _escape_ctx(ctx):
    for k, v in list(ctx.items()):
        if k.startswith('_') or k in RICH_CTX_KEYS or not isinstance(v, str):
            continue
        ctx[k] = sanitize_rich(v)
    return ctx


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
        # v3.9 精度修复：测算器 input 必须用服务端全精度值，不能用展示用的 2 位小数。
        # 否则「重置为原报告参数」后 FY3有效盈利/三年分红/合理股价与正文差 0.01~0.6 亿。
        'FY1_RAW': _raw(p['fy1']), 'FY2_RAW': _raw(p['fy2']), 'FY3_RAW': _raw(p['fy3']),
        'SHARES_RAW': _raw(p['total_shares'], 8), 'PAYOUT_RAW': _raw(p['payout'], 6),
        'PRICE_RAW': _raw(p['current_price'], 2),
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

    # ---- 一致预期 / 取数口径：结构化渲染（hero 只放短标签，长文进数据底座卡） ----
    cons = p.get('consensus') or {}
    ctx['CONSENSUS_LABEL'] = p.get('consensus_label') or short_label(p.get('consensus_src'))
    ctx['RF_LABEL'] = p.get('rf_label') or short_label(p.get('rf_src'), 22)
    ctx['CONSENSUS_TBL'] = render_consensus_tbl(cons)
    prov = []
    if not ctx['CONSENSUS_TBL']:
        prov.append(('一致预期源', render_clauses(p.get('consensus_src', ''))))
    if cons.get('cross_check'):
        prov.append(('交叉复核', render_clauses(cons['cross_check'])))
    if p.get('rf_src'):
        prov.append(('无风险利率', render_clauses(p['rf_src'])))
    prov += [tuple(x) for x in (p.get('prov_rows') or [])]
    ctx['PROV_ROWS'] = render_prov_rows(prov)
    _foot = []
    if p.get('report_date'):
        _foot.append(f'报告日期 {p["report_date"]}')
    if cons.get('as_of'):
        _foot.append(f'一致预期截面 {cons["as_of"]}')
    if cons.get('fetch_note'):
        _foot.append(cons['fetch_note'])
    ctx['PROV_FOOT'] = ' ｜ '.join(_foot)

    # ---- 系数校准说明：值位只放值 + 短标签，长解释一律进 .calib ----
    ctx['DISCOUNT_TAG'] = p.get('discount_tag') or '已校准'
    ctx['PAYOUT_TAG'] = p.get('payout_tag') or '已校准'
    ctx['PAYOUT_PCT'] = f"{p['payout'] * 100:.1f}%"

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
    # v3.14：显式给少于 7 项时补齐，避免模板固定的 {{CHK4..7}} 被误判成
    # 「模板未定义占位符」；真正缺项由 validate() 给出准确提示。
    checks = list(cr.get('checks') or [])
    checks += [''] * max(0, 7 - len(checks))
    for i, ch in enumerate(checks[:7], 1):
        ctx[f'CHK{i}'] = wrap_sem(ch)
    ctx['CROSS_CLASS'] = cr.get('class') or sem_class(cr.get('verdict', ''))

    # ---- 交叉验证卡片列表 + 结论框（替代窄表格塞长句） ----
    ctx['CROSS_ROWS'] = render_cross_rows(cr)
    ctx['VERDICT_BOX'] = render_verdict(cr.get('verdict', ''), ctx.get('CROSS_CLASS', ''))
    ctx['DISCOUNT_CALIB'] = render_calib(p.get('discount_calib'))
    ctx['PAYOUT_CALIB'] = render_calib(p.get('payout_calib'))

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
    ctx['DISCOUNT_NOTE'] = p.get('discount_note') or (
        '默认0.9；书建议打折因券商预期偏乐观，报告内已备注说明；可设1.0关闭')
    # 书中 30% 只是无历史数据时的兜底，不是默认值（见 SKILL.md §二·五 铁律 5）。
    # 缺省文案不得谎称「已取历史中位数」：没给 payout_note 就是没给依据。
    ctx['PAYOUT_NOTE'] = (p.get('payout_note') or p.get('payout_basis')
                          or '未附历史分红依据；书中 30% 仅为无数据兜底，港股/美股见适配块')

    # ---- 判断链（档位选择留痕：判据/事实/临界分析/对估值的影响） ----
    ctx['GROWTH_CHAIN'] = render_chain(p.get('growth_chain'))
    ctx['COMP_CHAIN'] = render_chain(p.get('comp_chain'))
    ctx['MOAT_CHAIN'] = render_chain(p.get('moat_chain'))
    ctx['CERT_CHAIN'] = render_chain(p.get('cert_chain'))
    # v3.10：数据来源汇总表（研报 / 搜索内容的可核查出处，缺省由 validate_params 阻断）
    ctx['SOURCES_TBL'] = render_sources(p.get('sources'))

    # 内部体检数据（不进模板，供 validate 使用）
    ctx['_prices'] = (pes_p, r['rprice'], opt_p)
    return _escape_ctx(ctx), r


# ---------- 输出前校验（阻断 / 告警） ----------
CERT_LIMITS = {
    'tech_score':   (0.0, 1.0, '技术颠覆风险（几乎不存在1／有一定0.5／很大0）'),
    'cycle_score':  (0.0, 1.0, '行业周期波动（波动很小1／中等0.5／很大0）'),
    'policy_score': (0.0, 2.0, '政策抑制风险（极小2／有一定1／较大0）'),
}


def render_chain(chain):
    """渲染「判断链」组件：档位选择的留痕（判据/事实/临界分析/影响）。
    chain: [[标签, HTML内容], ...]；缺省返回空串（模板占位符被替换为空）。"""
    if not chain:
        return ''
    rows = ''.join(f'<tr><th>{_T(k)}</th><td>{_T(v)}</td></tr>' for k, v in chain)
    return ('<div class="chain"><div class="chain-head">判断链 / RATING CHAIN · 为什么是这个档位</div>'
            f'<table class="chain-tbl">{rows}</table></div>')


def _g(v):
    """数字紧凑格式化（2.0→'2'、0.5→'0.5'），与前端 JS String(v) 行为对齐。"""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f'{v:g}'
    return str(v)


# v3.12：必需字段 / 枚举 / 数值类型。缺字段过去会直接抛 KeyError、非数字会抛
# TypeError、非法档位会抛 KeyError、非法 market 会静默删光所有市场适配块。
REQUIRED_FIELDS = ('company', 'code', 'market', 'growth', 'comp', 'moat',
                   'rf_rate', 'payout', 'total_shares', 'current_price',
                   'fy1', 'fy2', 'fy3')
NUM_FIELDS = ('fy1', 'fy2', 'fy3', 'rf_rate', 'payout', 'total_shares',
              'current_price', 'consensus_discount',
              'tech_score', 'cycle_score', 'policy_score')
MARKETS = ('A股', '港股', '美股')

# ---------- v3.13 出处引用一致性 / 分红率铁律 ----------
SRC_REF_RE = re.compile(r'\[(S\d{2,})\]')
SRC_ID_RE = re.compile(r'^S\d{2,}$')
_REF_SKIP_KEYS = ('output', 'url', 'id', 'tier', 'date', 'code', 'market', 'currency')


def _collect_refs(node, key=''):
    """递归收集 params 文本里的 [S##] 引用（排除 sources 自身与 URL / 路径类字段）。"""
    refs = set()
    if isinstance(node, dict):
        for k, v in node.items():
            if k == 'sources':        # 出处汇总自身的说明文本不算正文引用
                continue
            refs |= _collect_refs(v, k)
    elif isinstance(node, list):
        for v in node:
            refs |= _collect_refs(v, key)
    elif isinstance(node, str) and key not in _REF_SKIP_KEYS:
        refs.update(SRC_REF_RE.findall(node))
    return refs


def source_refs_audit(p):
    """正文 [S##] ↔ sources 编号一致性：errors 阻断 / warnings 提示。

    v3.10 只把「引用要能追到出处」写进契约，没有门禁：正文敲错 S19 或删了
    sources 条目，报告照样生成，读者点不到出处。现在未定义引用直接阻断。
    """
    errors, warns = [], []
    ids = []
    for i, s in enumerate(p.get('sources') or [], 1):
        if not isinstance(s, dict):
            continue
        # v3.14：与 render_sources() / validate_params() 用同一补号规则。
        # 省略 id 是合法输入，旧版在这里拿空串去匹配 SRC_ID_RE 会误杀。
        sid = str(s.get('id') or 'S%02d' % i).strip()
        ids.append(sid)
        if not SRC_ID_RE.match(sid):
            errors.append('P0-H 出处编号格式非法：%r（应为 S01 / S02…）' % sid)
        u = str(s.get('url') or '').strip()
        tier = str(s.get('tier') or '').strip()
        if u and tier and tier not in SRC_TIER_CLASS:
            errors.append('P0-H %s tier 分级非法：%r，可选 %s'
                         % (sid, tier, '/'.join(SRC_TIER_CLASS)))
        if not u and tier not in ('转引', '未采用'):
            errors.append('P0-H %s 无可核查链接，tier 必须标「转引」或「未采用」' % sid)
        if u and tier == '转引':
            warns.append('P0-H %s 有核查链接却标 tier=转引，请复核' % sid)
    refs = _collect_refs({k: v for k, v in p.items() if k != 'sources'})
    missing = sorted(refs - set(ids))
    if missing:
        errors.append('P0-H 正文引用了 sources 中不存在的编号：%s；禁止出现无出处引用'
                      % '、'.join(missing))
    unused = sorted(set(ids) - refs)
    if unused:
        warns.append('P0-H sources 有 %d 条未在正文以 [S##] 引用（%s）：'
                     '可保留作背景核查，交付前建议精简或补引用'
                     % (len(unused), '、'.join(unused)))
    return errors, warns


_WEAK_BASIS = {'x', 'X', '-', '--', '?', '0', '1', 'n/a', 'N/A', 'na', 'NA',
               'todo', 'TODO', '待补', '无', '测试', '备注'}


def payout_basis_audit(p):
    """分红率铁律（v3.6 提出 / v3.13 加强 / v3.14 堵占位符绕过）：
    无历史依据一律告警，>35% 直接阻断。payout_basis 必须是可审阅的依据文本，
    不能填 x / - / TODO 这类占位符就把 71% 的支付率放过去。
    """
    errors, warns = [], []
    basis = (str(p.get('payout_basis') or '').strip()
             or str(p.get('payout_note') or '').strip())
    pay = p.get('payout')
    if ((basis and len(basis) >= 2 and basis not in _WEAK_BASIS)
            or not isinstance(pay, (int, float)) or isinstance(pay, bool)):
        return errors, warns
    msg = ('股利支付率 %.4g 未附历史分红依据（payout_basis / payout_note 均为空）：'
           '须先查近 3–5 年分红/归母净利取中位数，禁止直接套书中 30%% 兜底值' % pay)
    if pay > 0.35:
        errors.append('P0-P ' + msg + '；且高于 35% 阈值，阻断输出')
    else:
        warns.append('⚠️ ' + msg)
    return errors, warns


def validate_params(p):
    """入参范围体检：errors 阻断输出。防止确定性超满分导致贴现率为负等崩坏。"""
    errors = []
    for k in REQUIRED_FIELDS:
        v = p.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            errors.append(f'必需字段缺失：{k}')
    for k in NUM_FIELDS:
        v = p.get(k)
        if v is None:
            continue
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            errors.append(f'{k} 必须为数字（当前 {v!r}）')
        elif not math.isfinite(float(v)):
            errors.append(f'{k} 必须为有限数，禁止 NaN / Infinity（当前 {v!r}）')
    for k, table in (('growth', BASE), ('comp', COMP), ('moat', MOAT)):
        v = p.get(k)
        if v is not None and v not in table:
            errors.append(f'{k} 档位非法：{v!r}，可选 {"/".join(table)}')
    if p.get('market') is not None and p['market'] not in MARKETS:
        errors.append(f'market 非法：{p["market"]!r}，可选 {"/".join(MARKETS)}')
    rf, pay = p.get('rf_rate'), p.get('payout')
    if isinstance(rf, (int, float)) and not isinstance(rf, bool) and not 0 <= rf < 0.5:
        errors.append(f'rf_rate 无风险利率异常：{_g(rf)}，应在 0~0.5 之间（小数形式）')
    if isinstance(pay, (int, float)) and not isinstance(pay, bool) and not 0 <= pay <= 1:
        errors.append(f'payout 支付率异常：{_g(pay)}，应在 0~1 之间（>100% 须人工复核）')
    if errors:
        return errors          # 前置校验不过时，后续数值比较会抛 TypeError，先返回
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
        v = p.get(k, 0)
        if not (v and v > 0):
            errors.append(f'{label} 预期盈利必须 > 0（当前 {_g(v)}）')
    if not (p.get('current_price', 0) and p['current_price'] > 0):
        errors.append(f"当前股价必须 > 0（当前 {_g(p.get('current_price'))}）")
    if not (p.get('total_shares', 0) and p['total_shares'] > 0):
        errors.append(f"总股本必须 > 0（当前 {_g(p.get('total_shares'))}）")
    d = p.get('consensus_discount', 0.9)
    if not (0 < d <= 1.5):
        errors.append(f'一致预期折扣应在 0~1.5 之间（当前 {_g(d)}）')
    # v3.10 出处硬约束：没有 sources 就出报告，等于让研报 / 搜索内容裸奔。
    srcs = p.get('sources')
    if not srcs:
        errors.append('P0-H 出处缺失：sources 为空 → 研报 / 搜索内容将无可核查出处')
    else:
        seen = set()
        for i, s in enumerate(srcs, 1):
            sid = str((s or {}).get('id') or 'S%02d' % i)
            if sid in seen:
                errors.append('P0-H 出处编号重复：%s' % sid)
            seen.add(sid)
            if not isinstance(s, dict):
                errors.append('P0-H %s 不是 dict' % sid)
                continue
            for f in ('use', 'name', 'value', 'caliber', 'date'):
                if not str(s.get(f) or '').strip():
                    errors.append('P0-H %s 缺字段 %s' % (sid, f))
            u = str(s.get('url') or '').strip()
            if u and not re.match(r'^https?://\S+$', u):
                errors.append('P0-H %s url 非 http(s) 或含空白：%s' % (sid, u[:60]))
    errors.extend(source_refs_audit(p)[0])
    return errors


def _plain(s):
    """去标签 / 实体 / 空白后的可见文本，用于判「纯 <br>、&nbsp; 冒充结论」。"""
    return re.sub(r'<[^>]*>|&[A-Za-z#0-9]+;|\s+', '', str(s or ''))


def validate(ctx):
    """返回 (errors, warnings)：errors 阻断输出，warnings 仅提示。"""
    errors, warns = [], []
    # v3.14：'<br>' / '&nbsp;' strip 后非空，旧版会把空结论当成已完成。
    if not _plain(ctx.get('CROSS_VERDICT', '')):
        errors.append('交叉验证结论 (CROSS_VERDICT) 为空 → 报告视为未完成，阻断输出')
    # v3.14：只判 verdict 字符串，7 项明细全空也能出「已完成交叉验证」的报告。
    if not str(ctx.get('CROSS_ROWS', '')).strip():
        errors.append('交叉验证七项明细 (CROSS_ROWS) 为空 → 报告视为未完成，阻断输出')
    empty_chk = [i for i in range(1, 8) if not str(ctx.get('CHK%d' % i, '')).strip()]
    if empty_chk:
        errors.append('七项强制交叉验证缺项：%s → 阻断输出'
                     % '、'.join('CHK%d' % i for i in empty_chk))
    if not str(ctx.get('COMPANY_NAME', '')).strip():
        errors.append('公司名称缺失')
    pes, base, opt = ctx.get('_prices', (0, 0, 0))
    if not (pes <= base + 1e-9 <= opt + 1e-9):
        warns.append(f'三档情景非单调（悲观{pes:.1f} / 基准{base:.1f} / 乐观{opt:.1f}），请复核情景规则')
    for key, name in (('GROWTH_CHAIN', '增长潜力'), ('COMP_CHAIN', '竞争格局'), ('MOAT_CHAIN', '护城河'),
                      ('CERT_CHAIN', '确定性得分')):
        if not str(ctx.get(key, '')).strip():
            warns.append(f'{name}档位缺少判断链（{key} 为空）：档位选择无可审阅的依据，建议补充判据/事实/临界分析')
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
    pa_errs, pa_warns = payout_basis_audit(params)
    pre = validate_params(params) + pa_errs
    if pre:
        raise SystemExit('❌ 入参体检不通过，阻断输出：\n- ' + '\n- '.join(pre))
    ctx, r = build_context(params)
    errors, warns = validate(ctx)
    # v3.13：分红率铁律（无依据一律告警、>35% 阻断）+ 出处引用未使用提示。
    warns = pa_warns + warns + source_refs_audit(params)[1]
    if errors:
        raise SystemExit('❌ 阻断输出：\n- ' + '\n- '.join(errors))
    with open(tpl_path, encoding='utf-8') as f:
        html = f.read()
    # v3.14：keep_market 用精确串比较，注释名一改就把三块全删、章节空白。
    market_names = [n.strip() for n in re.findall(r'<!-- MARKET:(.*?) -->', html)]
    if params['market'] not in market_names:
        raise SystemExit('❌ 模板市场块注释与 market=%r 不匹配（现有 %s），'
                         '继续生成会静默删光市场适配内容'
                         % (params['market'], market_names))
    html = keep_market(html, params['market'])
    # v3.12：未定义占位符过去被静默替换成破折号，且替换后再 findall 必然为空，
    # 「剩余未替换占位符：无」是假门禁。现在模板写错直接阻断。
    unknown = sorted({m.strip() for m in PH_RE.findall(html) if m.strip() not in ctx})
    if unknown:
        raise SystemExit('❌ 模板存在未定义占位符，阻断输出：\n- ' + '\n- '.join(unknown))
    html = PH_RE.sub(lambda m: str(ctx[m.group(1).strip()]), html)
    # 下拉当前项：按报告参数注入 selected（测算器需要）
    html = set_select(html, 'i-growth', params['growth'])
    html = set_select(html, 'i-comp', params['comp'])
    html = set_select(html, 'i-moat', params['moat'])
    # v3.9 默认 HYDRO PAPER v3.0 皮肤；skin='classic' 仅用于换肤前后对比，不用于交付。
    if params.get('skin', 'hydro') == 'hydro':
        html = apply_skin(html)
        skin_errs, skin_warns = validate_hydro(html)
        warns.extend(skin_warns)
        if skin_errs:
            raise SystemExit('❌ HYDRO 皮肤门禁不通过，阻断输出：\n- ' + '\n- '.join(skin_errs))
    left = sorted(set(PH_RE.findall(html)))
    # v3.14：参数文本里带 {{KEY}} 时，一次替换不会递归处理，旧版只打印不阻断。
    if left:
        raise SystemExit('❌ 报告仍残留未替换占位符，阻断输出：%s' % '、'.join(left))
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
    print(f"版式：{'HYDRO PAPER v3.0（三层 CSS 内联 · brand 页脚 · 零远程资源）' if params.get('skin','hydro')=='hydro' else 'classic（旧 navy-gold，仅供对比）'}")
    print(f"剩余未替换占位符：{left if left else '无'}")
    for w in warns:
        print(f"⚠️  {w}")


if __name__ == '__main__':
    main()
