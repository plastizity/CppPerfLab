"""Offline HTML presentation of the same versioned view used by Agent queries."""
import html
import json
import math
import os
from pathlib import Path
from urllib.parse import quote

from .errors import LabError
from .views import evidence_path


COLORS = ("#62b5ff", "#ffa868", "#a4db84", "#d0a3ff", "#ff829b")


def esc(value):
    return html.escape(str(value), quote=True)


def number(value):
    return "—" if value is None else format(value, ".6g")


def details(title, value):
    return '<details><summary>' + esc(title) + '</summary><pre>' + esc(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)) + '</pre></details>'


def table(headers, rows, *, searchable=False):
    head = '<thead><tr>' + ''.join('<th scope="col">' + esc(h) + '</th>' for h in headers) + '</tr></thead>'
    body = '<tbody>' + ''.join('<tr>' + ''.join('<td>' + str(cell) + '</td>' for cell in row) + '</tr>' for row in rows) + '</tbody>'
    control = '<label>筛选表格 <input type="search" class="table-search" placeholder="输入符号或文件名"></label>' if searchable else ''
    return '<div class="table-block">' + control + '<div class="scroll"><table>' + head + body + '</table></div></div>'


def line_plot(series, x_label, y_label, *, log=False):
    """Plot recorded values; coordinate transforms are presentation, not analysis."""
    points = [point for s in series for point in s["points"]]
    if not points:
        return '<p class="muted">没有可绘制样本。</p>'
    if log and any(x <= 0 or y <= 0 for x, y in points):
        return '<p class="muted">非正值无法使用对数坐标；查看原始数据表。</p>'
    transform = math.log10 if log else lambda v: v
    xs, ys = [transform(p[0]) for p in points], [transform(p[1]) for p in points]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    if xmax == xmin:
        xmin, xmax = xmin - .5, xmax + .5
    if ymax == ymin:
        delta = abs(ymin) * .05 or 1
        ymin, ymax = ymin - delta, ymax + delta
    def position(x, y):
        return 90 + (transform(x) - xmin) / (xmax - xmin) * 690, 260 - (transform(y) - ymin) / (ymax - ymin) * 210
    svg = ['<svg class="plot" viewBox="0 0 840 330" role="img" aria-label="' + esc(x_label + ' / ' + y_label) + '">',
           '<path d="M90 40V260H795" fill="none" stroke="#71849c"/>']
    for i in range(5):
        fraction = i / 4
        x, y = 90 + 690 * fraction, 260 - 210 * fraction
        xv, yv = xmin + (xmax - xmin) * fraction, ymin + (ymax - ymin) * fraction
        if log:
            xv, yv = 10 ** xv, 10 ** yv
        svg.extend([f'<path d="M90 {y:.2f}H780" stroke="#29384c"/>',
                    f'<text x="82" y="{y + 4:.2f}" text-anchor="end">{esc(number(yv))}</text>',
                    f'<text x="{x:.2f}" y="282" text-anchor="middle">{esc(number(xv))}</text>'])
    for index, s in enumerate(series):
        color = COLORS[index % len(COLORS)]
        coordinates = [position(x, y) for x, y in s["points"]]
        if s.get("connect"):
            svg.append('<polyline fill="none" stroke="' + color + '" points="' + ' '.join(f'{x:.2f},{y:.2f}' for x, y in coordinates) + '"/>')
        for (x, y), (rawx, rawy) in zip(coordinates, s["points"]):
            svg.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="{color}"><title>' +
                       esc(s["label"] + ': ' + number(rawx) + ', ' + number(rawy)) + '</title></circle>')
    svg.extend(['<text x="440" y="316" text-anchor="middle">' + esc(x_label + ('（log10）' if log else '')) + '</text>',
                '<text x="95" y="24">' + esc(y_label + ('（log10）' if log else '')) + '</text>', '</svg>'])
    legend = '<p class="legend">' + ' '.join('<span style="color:' + COLORS[i % len(COLORS)] + '">● ' + esc(s["label"]) + '</span>' for i, s in enumerate(series)) + '</p>'
    return legend + ''.join(svg)


def metrics_section(view):
    result = []
    for group in view["metrics"]:
        s = group["summary"]
        source = ' · '+group['run_id'] if 'run_id' in group else ''
        result.append('<article><h3>' + esc(s["name"]+source) + '</h3><p>' + esc(s["unit"] + ' · ' + s["method"]) + '</p><p class="muted">范围：' + esc(s["scope"]) + '</p>')
        result.append(table(['样本数', '中位数', '最小', '最大', 'CV'], [[esc(s["n"]), number(s["median"]), number(s["min"]), number(s["max"]), number(s["cv"])]]))
        series = [{"label": s["name"], "points": [(p["index"], p["value"]) for p in group["samples"]]}]
        result.append(line_plot(series, '重复样本序号', s["unit"]))
        result.append(details('查看所有原始样本值与证据 ID', group["samples"]) + '</article>')
    return ''.join(result) or '<p class="muted">该记录没有正常化计量样本。</p>'


def performance_section(view):
    summary = view['summary']
    content = ['<p class="notice">工作量、指令执行质量、资源和耗时分别判断；不计算总分。指令数量减少不依赖 SIMD 或缓存指标，也不等同于实测加速。</p>']
    content.append(table(['维度', '覆盖状态', '观测数', '比较数', '未采集指标'], [
        [esc(d['title']), esc(d['coverage']), str(len(d['observations'])), str(len(d['comparisons'])),
         esc(', '.join(d['missing_metrics']))] for d in summary['dimensions']]))
    for finding in summary['findings']:
        content.append('<article><p class="status">'+esc(finding['outcome'])+'</p><p>'+esc(finding['message'])+'</p>'+details('结论依据', finding)+'</article>')
    for d in summary['dimensions']:
        if d['observations']:
            content.append(details(d['title']+'：观测与来源', d['observations']))
    if summary['derived_metrics']:
        content.append(details('配对硬件计数推导的 IPC', summary['derived_metrics']))
    content.append(details('总结限制', summary['limitations']))
    return ''.join(content)


def hotspot_section(view):
    # Show per-sample rows, never an invented cross-run sum/mean of percentages.
    rows = []
    for h in view["hotspots"]:
        percent = h["self_percent"]
        bar = '<div class="bar"><span style="width:' + str(min(100, max(0, percent))) + '%"></span></div>'
        rows.append([esc(h.get("sample", '—')), esc(h["symbol"]), esc(h["dso"]),
                     number(percent) + '%' + bar, esc(h["evidence_id"])])
    return '<p class="muted">各采样重复的 self 占比，分别展示；不是墙钟时间，不能跨重复相加。完整栈见原始 perf 文件。</p>' + (
        table(['重复', '符号', '模块', 'self %', '证据 ID'], rows, searchable=True) if rows else '<p>没有 CPU 采样热点。</p>')


def comparisons_section(view):
    content = []
    for c in view["comparisons"]:
        content.append('<article><h3>' + esc(c["metric"]) + (': scale=' + esc(c["scale"]) if 'scale' in c else '') + '</h3>')
        if not c["comparable"]:
            content.append('<p class="alert">不可比：未计算加速比。</p>' + details('测量条件差异', c["error"]["details"]) + '</article>')
            continue
        content.append('<p class="status">' + esc(c["classification"]) + '</p>')
        content.append(table(['组', '中位数 (' + c["unit"] + ')', '重复数', 'CV'], [
            ['Baseline', number(c["baseline_median"]), str(len(c["baseline_samples"])), number(c["coefficient_of_variation"][0])],
            ['Candidate', number(c["candidate_median"]), str(len(c["candidate_samples"])), number(c["coefficient_of_variation"][1])]]))
        content.append('<p>观测中位数比：' + number(c["speedup"]) + '；描述性 bootstrap 95% 区间：' + esc(c["bootstrap_95_percent_speedup"]) + '</p>')
        content.append(line_plot([{"label": label, "points": list(enumerate(c[key]))} for label, key in
                                  [('Baseline', 'baseline_samples'), ('Candidate', 'candidate_samples')]], '组内重复序号（非配对）', c["unit"]))
        content.append(details('比较政策与构建差异', {"policy": c["policy"], "treatment": c["treatment"]}))
        content.append(details('完整比较数据', c) + '</article>')
    return ''.join(content) or '<p class="muted">没有比较结果。</p>'


def growth_section(view):
    content = ['<p class="alert">经验增长模型只描述测量范围，不是 Big-O 数学证明。模型排序使用已保存的拟合结果。</p>']
    # Different metric meanings stay in different plots.
    grouped = {}
    for series in view["growth"]:
        for p in series["points"]:
            key = tuple(p[k] for k in ('name', 'unit', 'method', 'scope'))
            grouped.setdefault(key, {}).setdefault(series["variant"], []).append(p)
    for (name, unit, method, scope), variants in grouped.items():
        content.append('<h3>' + esc(name) + '</h3><p>' + esc(method + ' · ' + scope) + '</p>')
        series = [{"label": variant, "connect": True, "points": [(p["x"], p["median"]) for p in sorted(points, key=lambda p: p["x"])]}
                  for variant, points in variants.items()]
        content.append('<div class="scale-switch"><label>坐标 <select><option value="linear">线性</option><option value="log">对数</option></select></label>' +
                       '<div data-scale="linear">' + line_plot(series, '输入特征规模', unit) + '</div>' +
                       '<div data-scale="log" hidden>' + line_plot(series, '输入特征规模', unit, log=True) + '</div></div>')
    for item in view["growth"]:
        content.append('<article><h3>' + esc(item["variant"]) + '</h3>')
        content.append(table(['规模', '中位数', '单位', '重复数', 'CV', 'Run ID'], [[esc(p["x"]), number(p["median"]), esc(p["unit"]), esc(p["n"]), number(p["cv"]), esc(p["run_id"])] for p in item["points"]]))
        if 'models' in item:
            content.append(table(['候选模型', '拟合表达式', '系数', '截距', 'R²', '相对 RMSE', '留一预测相对 RMSE'], [[esc(m["model"]), esc(m['expression']) if 'expression' in m else '—', number(m["scale_coefficient"]), number(m["intercept"]), number(m["r_squared"]), number(m["relative_rmse"]), number(m["leave_one_out_relative_rmse"])] for m in item["models"]]))
        content.append(details('局部斜率、拟合限制及测量点', item) + '</article>')
    return ''.join(content)


CSS = """
:root{color-scheme:dark;background:#101722;color:#e8eff9;font:16px/1.6 system-ui,sans-serif}
body{margin:0}main{max-width:1200px;margin:auto;padding:28px}h1{font-size:28px}h2{border-bottom:1px solid #35465e;padding-bottom:8px}
a{color:#85c8ff}nav{display:flex;flex-wrap:wrap;gap:18px;position:sticky;top:0;background:#101722;padding:12px 0;z-index:2}
article,details,.notice{background:#182435;border:1px solid #35465e;border-radius:8px;padding:16px;margin:16px 0}
.muted{color:#b1bfd2}.alert,.status{color:#ffd58a}.error{color:#ff9b9b}.badge{display:inline-block;padding:3px 12px;background:#263b52;border-radius:20px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}summary{cursor:pointer}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;border-bottom:1px solid #35465e;padding:10px;vertical-align:top}th{color:#b8d8f7}td{overflow-wrap:anywhere;max-width:520px}
input,select,button{font:inherit;background:#22334a;color:#edf4ff;border:1px solid #6a829f;border-radius:4px;padding:6px;margin:6px}
.plot{width:100%;min-width:340px;max-height:440px}svg text{fill:#c5d4e7;font-size:12px}.legend span{margin-right:16px}
.bar{background:#26364b;width:140px;height:7px;margin-top:8px}.bar span{display:block;background:#62b5ff;height:100%}
[hidden]{display:none!important}footer{color:#b1bfd2;margin-top:32px}@media(max-width:600px){main{padding:14px}nav{position:static}.plot{min-width:0}}
"""

JS = """
document.querySelectorAll('.table-search').forEach(input=>input.addEventListener('input',()=>{
 const value=input.value.toLocaleLowerCase();
 input.closest('.table-block').querySelectorAll('tbody tr').forEach(row=>{row.hidden=!row.textContent.toLocaleLowerCase().includes(value);});
}));
document.querySelectorAll('.scale-switch select').forEach(select=>select.addEventListener('change',()=>{
 select.closest('.scale-switch').querySelectorAll('[data-scale]').forEach(plot=>{plot.hidden=plot.dataset.scale!==select.value;});
}));
document.getElementById('download-view').addEventListener('click',()=>{
 const text=document.getElementById('view-json').textContent;
 const url=URL.createObjectURL(new Blob([text],{type:'application/json'}));
 const anchor=document.createElement('a');anchor.href=url;anchor.download='cppperflab-analysis.json';anchor.click();URL.revokeObjectURL(url);
});
"""


def render_html(view, store, output):
    output = Path(output).resolve()
    evidence = []
    for entry in view["evidence"]:
        path, _ = evidence_path(store, entry["run_id"], entry["path"])
        href = quote(os.path.relpath(path, output.parent), safe='/')
        evidence.append([esc(entry["run_id"]), '<a href="' + esc(href) + '">' + esc(entry["path"]) + '</a>',
                         esc(entry["bytes"]), '<code>' + esc(entry["sha256"]) + '</code>'])
    quality = ''.join('<li class="' + ('error' if q['severity'] == 'error' else 'alert') + '"><strong>' +
                      esc(q["code"]) + '</strong> ' + esc(q["message"]) + details('详情', q) + '</li>' for q in view["quality"])
    title = 'CppPerfLab · ' + view["view_kind"] + ' · ' + view["record_id"]
    overview_rows = [[esc(key), esc(value) if not isinstance(value, (dict, list)) else details('展开', value)]
                     for key, value in view['overview'].items()]
    sections = [('overview', '概览', table(['项目', '记录值'], overview_rows) + details('运行及构建条件', view["conditions"])),
                ('performance', '分析与总结', performance_section(view)),
                ('quality', '可信度与限制', '<ul>' + quality + '</ul>' + '<ul>' + ''.join('<li>' + esc(x) + '</li>' for x in view["limitations"]) + '</ul>')]
    if view['error']:
        sections[2] = (sections[2][0], sections[2][1], details('实验错误', view['error']) + sections[2][2])
    if view['view_kind'] in ('run', 'summary'):
        sections.extend([('metrics', '指标与样本', metrics_section(view)), ('hotspots', '采样热点', hotspot_section(view)),
                         ('reasoning', '事实与待验证假设', details('观测事实', view['facts']) + details('待验证假设', view['hypotheses']))])
    if view['view_kind'] == 'study':
        sections.append(('growth', '规模增长', growth_section(view)))
    if view['view_kind'] != 'run':
        sections.append(('comparison', '实验比较', comparisons_section(view)))
    sections.append(('evidence', '原始证据', '<p>链接指向本机实验归档；移动 HTML 后需保留其相对位置。哈希为归档记录值，点击链接不执行校验；Agent evidence 查询执行校验。</p>' + table(['Run ID', '文件', '字节数', '记录 SHA-256'], evidence, searchable=True)))
    content = ''.join('<section id="' + sid + '"><h2>' + label + '</h2>' + body + '</section>' for sid, label, body in sections)
    snapshot = details('统一分析 JSON（与 Agent 全量查询相同）', view)
    # Escaped text inside a pre is never interpreted as executable JavaScript.
    snapshot = snapshot.replace('<pre>', '<pre id="view-json">', 1)
    return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + esc(title) + '</title><style>' + CSS + '</style></head><body><main><h1>' + esc(title) + '</h1><p><span class="badge">' + esc(view['status']) + '</span> · 优化未自动批准</p><p class="notice">本报告展示已保存的测量与分析。采样占比、进程耗时、迭代耗时和堆占用保留各自语义。</p><nav>' + ''.join('<a href="#' + sid + '">' + label + '</a>' for sid, label, _ in sections) + '</nav>' + content + '<button id="download-view" type="button">下载统一分析 JSON</button>' + snapshot + '<footer>离线报告 · 无网络依赖 · 图表只呈现已保存数据 · 无完整调用栈时不生成火焰图</footer></main><script>' + JS + '</script></body></html>'


def export_html(view, store, output):
    output = Path(output).resolve()
    # An explicit export must not overwrite the authoritative archived evidence.
    if output.is_relative_to(store.root):
        raise LabError("invalid_request", "Export HTML outside the authoritative experiment store")
    document = render_html(view, store, output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding='utf-8')
    return {"schema_version": 1, "record_id": view["record_id"], "view_kind": view["view_kind"],
            "status": view["status"], "path": str(output), "error": view['error']}
