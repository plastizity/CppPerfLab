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
                     number(percent) + '%' + bar, (number(h['inclusive_percent'])+'%') if 'inclusive_percent' in h else '—',esc(h["evidence_id"])])
    return '<p class="muted">各采样重复的 self 占比，分别展示；不是墙钟时间，不能跨重复相加。完整栈见原始 perf 文件。</p>' + (
        table(['重复', '符号', '模块', 'self %', 'observed-chain inclusive %', '证据 ID'], rows, searchable=True) if rows else '<p>没有 CPU 采样热点。</p>')


def phases_section(view):
    notice='<p class="notice">包含时间不能跨父子重复相加。自身时间仅在声明子阶段覆盖完整时提供；线程累计时间不等于墙钟时间。结果分类不等于正确性。</p>'
    return notice+table(['实验 / 测量轮次','阶段路径','轨道 / 调用','输入 ID','结果 / 正确性','包含秒数','自身秒数','未归属秒数'],[
        [esc(p.get('run_id',view['record_id'])+' / '+p.get('execution_stage','measurement')+' / '+str(p['sample'])),esc(' / '.join(p['path'])),esc(p['track_id']+' / '+p['call_id']),esc(p['input_id']),
         esc(p['result']+' / '+p['correctness']),number(p['inclusive_seconds']),number(p['self_seconds']),number(p['unattributed_seconds'])]
        for p in view['phases']])+details('完整阶段记录、工作量与证据',view['phases'])


def thread_activity_section(view):
    content=['<p class="notice">应用工作/等待是程序显式记录的墙钟作用域，不是精确 CPU 运行或 off-CPU 时间。空白区间未观测；轨道时间不可直接当作全过程耗时。</p>']
    colors={'application_work':'#62b5ff','application_wait':'#ffa868','unclassified':'#a8a8a8'}
    labels={'application_work':'应用工作','application_wait':'应用等待','unclassified':'未标注'}
    for item in view['thread_timelines']:
        content.append('<h3>'+esc(item.get('run_id',view['record_id']))+' / '+esc(item['execution_stage'])+' / '+esc(item.get('sample',item.get('measurement_group')))+'</h3>')
        content.append(table(['轨道 / PID:TID','观测秒数','工作秒数','等待秒数','未标注秒数','等待占比','标注覆盖','证据状态 / 正确性'],[
            [esc(t['track_id']+' / '+str(t['pid'])+':'+str(t['tid'])),number(t['observed_seconds']),number(t['work_seconds']),
             number(t['wait_seconds']),number(t['unclassified_seconds']),number(t['wait_fraction']),number(t['annotation_coverage_fraction']),
             esc(t['identity_state']+' / '+t['correctness_status'])] for t in item['tracks']]))
        intervals=[v for t in item['tracks'] for v in t.get('intervals',[])]
        if intervals:
            start=min(v['start_ns'] for v in intervals);end=max(v['end_ns'] for v in intervals)
            if end>start:
                content.append('<p>蓝：应用工作；橙：应用等待；灰：已观测但未标注。悬停查看区间、原因和阶段 ID。</p>')
                for track in item['tracks']:
                    bars=[]
                    for event in track.get('intervals',[]):
                        left=100*(event['start_ns']-start)/(end-start);width=100*(event['end_ns']-event['start_ns'])/(end-start)
                        title=labels[event['kind']]+' / '+str(event['reason'])+' / '+str(event['start_ns'])+'..'+str(event['end_ns'])+' ns / '+','.join(event['phase_ids'])
                        bars.append('<span title="'+esc(title)+'" style="position:absolute;top:0;height:20px;left:'+format(left,'.9g')+'%;width:'+format(width,'.9g')+'%;background:'+colors[event['kind']]+'"></span>')
                    content.append('<div>'+esc(track['track_id'])+'</div><div style="position:relative;height:20px;background:#f5f5f5;border:1px solid #bbb">'+''.join(bars)+'</div>')
        content.append(details('应用等待原因、原始证据与 OS 数据限制',item))
    return ''.join(content)


def scheduler_section(view):
    content=['<p class="notice">Linux perf context-switch records provide intervals only between recorded switch boundaries. Capture edges are censored. They do not report wakeups, runqueue delay, blocking cause or lock contention; per-thread durations are not process utilization.</p>']
    for item in view['scheduler_timelines']:
        content.append('<h3>'+esc(item.get('run_id',view['record_id']))+' / '+esc(item['execution_stage'])+' / '+esc(item.get('measurement_group'))+'</h3>')
        content.append('<p>调度证据状态：'+esc(item['evidence_state'])+'。表内时长只累计闭合的 switch 区间，采集前后均删失。</p>')
        content.append(table(['PID:TID / generation','切换事件数','闭合区间','on-CPU 秒','off-CPU 秒','边界','证据状态'],[
            [esc(str(t['pid'])+':'+str(t['tid'])+' / '+str(t['generation'])),esc(t['switch_event_count']),
             esc(t['closed_interval_count']),number(t['on_cpu_seconds']),number(t['off_cpu_seconds']),
             esc('采集边界删失' if t['left_censored'] and t['right_censored'] else '见轨道证据'),esc(item['evidence_state'])]
            for t in item['tracks']]))
        content.append(details('OS 调度证据、问题与来源',item))
    for selection in view.get('scheduler_queries',[]):
        page=selection['interval_page']
        content.append('<p>查询状态：'+esc(selection['evidence_state'])+' / '+esc(selection['selection_status'])+
            '。'+esc(selection.get('reason') or '读取所选事件页。')+'；显示所选分页，不代表完整线程生命周期。'+
            (' 后续区间仍未显示。' if page['truncated'] else '')+'</p>')
        content.append('<h3>已选区间 '+esc(page['offset'])+'..'+esc(page['offset']+page['returned'])+' / '+esc(page['total'])+'</h3>')
        rows=selection['intervals']
        if rows:
            start=min(row['start_ns'] for row in rows);end=max(row['end_ns'] for row in rows)
            if end>start:
                content.append('<p>共同时间轴只覆盖当前页。蓝色表示 on-CPU 区间，橙色表示 off-CPU 区间；边界仍有删失。</p>')
                colors={'on_cpu':'#62b5ff','off_cpu':'#ffa868'}
                grouped={}
                for row in rows:grouped.setdefault(row['track_id'],[]).append(row)
                for track_id,track_rows in grouped.items():
                    bars=[]
                    for row in track_rows:
                        left=100*(row['start_ns']-start)/(end-start)
                        width=100*(row['end_ns']-row['start_ns'])/(end-start)
                        title=row['state']+' / '+str(row['start_ns'])+'..'+str(row['end_ns'])+' ns / events '+str(row['start_line'])+'..'+str(row['end_line'])
                        bars.append('<span title="'+esc(title)+'" style="position:absolute;top:0;height:20px;left:'+format(left,'.9g')+
                            '%;width:'+format(width,'.9g')+'%;background:'+colors[row['state']]+'"></span>')
                    content.append('<div>'+esc(track_id)+'</div><div style="position:relative;height:20px;background:#f5f5f5;border:1px solid #bbb">'+''.join(bars)+'</div>')
        content.append(table(['PID:TID / generation','CPU 状态','开始 ns','结束 ns','持续 ns','原始事件行'],[
            [esc(row['track_id']),esc(row['state']),esc(row['start_ns']),esc(row['end_ns']),
             esc(row['duration_ns']),esc(str(row['start_line'])+'..'+str(row['end_line']))]
            for row in rows]))
        content.append(details('当前页调度区间来源',selection))
    return ''.join(content)


def phase_summary_section(view):
    notice='<p class="notice">阶段汇总保留包含耗时、自身耗时和调用次数的原始口径。父子包含耗时不能重复相加；缺失不是零。只有汇总日志时不生成时间线或未覆盖时间。</p>'
    rows=[]
    for p in view['phase_summaries']:
        rows.append([esc(p.get('run_id',view['record_id'])+' / '+p['execution_stage']+' / '+str(p['sample'])),
            esc(' / '.join(p['path'])+' · '+p['name']),number(p['inclusive_seconds']),esc(p['inclusive_evidence_state']),
            number(p['self_seconds']),esc(p['self_evidence_state']+' / '+p['self_method']),number(p['calls']),
            esc(p['correctness']),esc(str(p['source_lines']['first'])+'..'+str(p['source_lines']['last']))])
    return notice+table(['实验 / 轮次','统一阶段路径 / 名称','包含秒数','包含证据','自身秒数','自身来源','调用次数','正确性','原日志行'],rows)+details('阶段工作量和可追溯证据',view['phase_summaries'])


def lifecycle_section(view):
    current=view.get('lifecycle');body=''
    if current is not None and current.get('record_kind')=='study':
        progress=current['progress']
        labels=[('planned_runs','计划 Run'),('created_runs','已创建 Run'),('completed_runs','已完成 Run'),
                ('failed_runs','失败 Run'),('cancelled_runs','取消 Run'),('timeout_runs','超时 Run'),
                ('confirmed_running_runs','已核实运行中的 Run'),('owner_exited_records','执行者已退出的记录'),
                ('unverified_running_records','存活未核实的记录'),('normalized_samples','已保存测量样本')]
        body+=table(['进度口径','数量'],[[esc(label),esc(progress[key])] for key,label in labels])
        body+=table(['执行链','记录 ID','已保存状态'],[[esc(row['record_kind']),esc(row['record_id']),esc(row['status'])] for row in current['active_path']])
        if len(current.get('active_paths', [])) > 1:
            body+='<h4>并行活动执行链</h4>'
            for index,path in enumerate(current['active_paths'],1):
                body+='<p>活动链 '+esc(index)+'</p>'+table(['记录','记录 ID','已保存状态'],[
                    [esc(row['record_kind']),esc(row['record_id']),esc(row['status'])] for row in path])
        body+=table(['子 Run','结果状态','当前存活核验','候选处置','替代结果'],[
            [esc(row['run_id']),esc(row['run_status']),esc(row['observed_state']),
             esc(row['candidate']['candidate_state']),esc(row['candidate']['superseded_by'])] for row in current['runs']])
        body+='<p>计数不代表等量工作或优化验收；全过程 ETA：'+esc(current['eta']['evidence_state'])+'。</p>'
    elif current is not None:
        eta=current['workflow_eta']
        body+=table(['全过程 ETA 证据','预计剩余秒数','已用秒数','匹配历史次数'],[
            [esc(eta['evidence_state']),number(eta['total_remaining_seconds']),number(eta['elapsed_seconds']),esc(eta['n'])]])
        body+='<p>'+esc(eta['reason'])+'。这是平台工作流成本估计，历史范围不是置信区间，也不是目标程序性能指标。</p>'
        body+=details('全过程 ETA 的来源与限制',eta)
    return body+details('运行状态与存活核验',current)+details('候选保留、撤回与替代历史',view.get('candidate'))


def sampling_section(view):
    profiles=view['sampling_profiles']
    content=['<p class="notice">可解释性与性能收益分开。比例按已记录的 CPU 事件周期加权；部分调用链不证明完整展栈。</p>']
    content.append(table(['Run / 阶段 / 启动组','样本数','已解析叶帧比例','未解析叶帧比例','前两帧已解析比例','记录的丢失数','证据状态'],[
        [esc(p.get('run_id',view['record_id'])+' / '+p['execution_stage']+' / '+str(p['measurement_group'])),esc(p['quality']['sample_count']),
         number(p['quality']['resolved_self_fraction']),number(p['quality']['unresolved_self_fraction']),number(p['quality']['resolved_two_frame_fraction']),esc(p['quality']['lost_events']),esc(p['evidence_state'])] for p in profiles]))
    precheck=view['conditions'].get('sampling_precheck')
    if precheck:
        content.append('<p>目标预检：'+esc(precheck['status'])+'</p>')
        content.append(table(['预检项','状态','观测值','预设门槛'],[[esc(k),esc(c['status']),number(c.get('observed')),number(c.get('threshold'))] for k,c in precheck['checks'].items()]))
    content.append(details('时钟、ELF/工具来源及原始样本引用',profiles))
    return ''.join(content)


def cpu_selection_section(view):
    selection=view['cpu_selection']
    result='<p class="notice">筛选状态不是性能验收。函数自身/包含占比使用同一筛选后的事件周期分母，包含占比只覆盖已观察调用链。</p>'
    denominator=selection['denominator']
    if denominator:
        result+=table(['筛选状态','选中/整次样本数','选中/整次记录周期','记录周期覆盖比','未解析叶帧比例'],[[esc(selection['selection_status']),
            esc(str(denominator['selected_sample_count'])+' / '+str(denominator['whole_sample_count'])),
            esc(str(denominator['selected_recorded_period'])+' / '+str(denominator['whole_recorded_period'])),
            number(denominator['selected_fraction_of_recorded_period']),number(denominator['unresolved_selected_self_fraction'])]])
    return result+details('筛选条件、分母、未解析比例和覆盖限制',selection)


def runtime_identity_section(view):
    reports=view['runtime_identities']
    content=['<p class="notice">这是运行时 /proc 执行文件与文件映射观测。周期快照可能遗漏短进程和瞬时加载；文件哈希不是内存代码哈希，预检观测不证明后续测量的加载情况。</p>']
    content.append(table(['Run / 阶段 / 测量启动组','证据状态','目标快照数','观察到的实际执行文件','对象数','身份验收','截断'],[
        [esc(r.get('run_id',view['record_id'])+' / '+r['execution_stage']+' / '+str(r['measurement_group'])),
         esc(r['evidence_state']),esc(r['target_snapshots']),esc(', '.join(sorted({p['actual_executable'] for p in r['actual_executables']}))),
         esc(len(r['objects'])),esc(r['acceptance']['status']),esc(r['truncations'])] for r in reports]))
    content.append(table(['阶段 / 启动组','PID / start ticks','实际路径','来源','设备 / inode','哈希状态','SHA-256'],[
        [esc(r['execution_stage']+' / '+str(r['measurement_group'])),esc(str(o['pid'])+' / '+str(o['start_ticks'])),esc(o['path']),
         esc(o['sources']),esc(str(o['device'])+' / '+str(o['inode'])),esc(o['hash']['evidence_state']),esc(o['hash']['sha256'])]
        for r in reports for o in r['objects']]))
    content.append(details('原始快照引用、身份规则及观测开销',reports))
    return ''.join(content)


def comparisons_section(view):
    content = []
    for c in view["comparisons"]:
        content.append('<article><h3>' + esc(c["metric"]) + (' · '+esc(c['case_id']) if 'case_id' in c else '') + (': scale=' + esc(c["scale"]) if 'scale' in c else '') + '</h3>')
        if c.get('execution_context',{}).get('kind')=='concurrent_ab':
            content.append('<p class="notice">当前结果表示两版本同时运行、共同竞争资源时的相对表现。隔离后的串行表现需要单独测量。</p>')
            content.append(details('并发竞争条件',c['execution_context']))
        from .comparison import normalized_cost, decisions
        verdict=decisions(c)
        labels={'correctness':'正确性','comparability':'可比性','statistics':'统计筛查','non_regression':'回退门槛','performance_target':'预设目标','improvement':'加速证据'}
        content.append(table(['判断项','结论','依据'],[[label,esc(verdict[key]['status']),esc(verdict[key]['reason'])] for key,label in labels.items()]))
        content.append('<p>回退门槛通过不代表确认加速；局部改善不代表全过程改善。</p>')
        content.append(details("归一化性能成本（基线 1，越小越好）与验收", normalized_cost(c)))
        if not c["comparable"]:
            content.append('<p class="alert">不可比：未计算加速比。</p>' + details('测量条件差异', c["error"]["details"]) + '</article>')
            continue
        content.append('<p class="status">' + esc(c["classification"]) + '</p>')
        content.append(table(['组', '中位数 (' + c["unit"] + ')', '区块数' if c['policy'].get('paired') else '重复数', 'CV'], [
            ['Baseline', number(c["baseline_median"]), str(len(c["baseline_samples"])), number(c["coefficient_of_variation"][0])],
            ['Candidate', number(c["candidate_median"]), str(len(c["candidate_samples"])), number(c["coefficient_of_variation"][1])]]))
        content.append('<p>观测中位数比：' + number(c["speedup"]) + '；描述性 bootstrap 95% 区间：' + esc(c["bootstrap_95_percent_speedup"]) + '</p>')
        content.append(line_plot([{"label": label, "points": list(enumerate(c[key]))} for label, key in
                                  [('Baseline', 'baseline_samples'), ('Candidate', 'candidate_samples')]], '配对区块序号' if c['policy'].get('paired') else '组内重复序号（非配对）', c["unit"]))
        if c['policy'].get('paired'):
            content.append(details('配对区块与原始来源',{'estimator':c['estimator'],'paired_ratio_cv':c['paired_ratio_cv'],'blocks':c['block_pairs']}))
        content.append(details('比较政策与构建差异', {"policy": c["policy"], "treatment": c["treatment"]}))
        content.append(details('完整比较数据', c) + '</article>')
    return ''.join(content) or '<p class="muted">没有比较结果。</p>'


def cases_section(view):
    return '<p class="notice">捕获时的耗时只用于诊断选样，不能当作独立回放的生产基线。各 case 与工具范围分开判断。</p>'+table(
        ['样本','状态','捕获分类','捕获正确性'],[[esc(c['sample_id']),esc(c['status']),esc(c['capture_result']),esc(c['capture_correctness'])] for c in view['overview']['cases']])+details('运行顺序与预算',view['conditions'])


def assessment_section(view):
    assessment=view['overview']['assessment']
    content=['<p class="notice">联合验收、加速证据分别判断。不同 profiler 的时间和内存保留各自范围，不混合统计。</p>']
    content.append(table(['判断项','结论'],[['正确性',esc(assessment.get('correctness','INCONCLUSIVE'))],['联合验收',esc(assessment['acceptance'])],
        ['全过程加速',esc(assessment['end_to_end_improvement'])]]))
    content.append(table(['输入','正确性','联合验收','全过程加速','工作量归因'],[
        [esc(c['case_id']),esc(c['correctness']),esc(c['acceptance']),esc(c['end_to_end_improvement']),esc(c['work_attribution'])]
        for c in assessment['cases']]))
    content.append(table(['输入','采集配置','角色','指标及范围','基线中位数','候选中位数','单位','验收门','门结果'],[
        [esc(c['case_id']),esc(c['profile_id']),esc(c['assessment_role']),esc(c['metric']+' / '+str(c.get('metric_scope'))),
         number(c['baseline_median']),number(c['candidate_median']),esc(c['unit']),esc(c['assessment_gate']),
         esc(c['decisions'][c['assessment_gate']]['status']) if c['assessment_gate']!='informational' else 'INFORMATIONAL']
        for c in view['comparisons']]))
    content.append(details('完整联合结论与工作量',assessment))
    content.append(details('采集任务、子 Study 与未执行项',view['overview']['jobs']))
    content.append(details('预先声明的验收条件、运行预算与版本指纹',view['conditions']))
    return ''.join(content)


def paired_section(view):
    return '<p class="notice">串行交错；资源隔离未强制。ABBA 作为一个区块，不能当作两个独立配对。</p>'+table(
        ['区块','预设顺序','区块状态','位置','版本','Run ID','成员状态'],[
        [esc(b['block_id']),esc(b['order']),esc(b['status']),esc(m['slot']),esc(m['variant']),esc(m['run_id']),esc(m['status'])]
        for b in view['overview']['blocks'] for m in b['members']])+details('完整区块及未执行计划',view['overview']['blocks'])+details('运行预算与资源条件',view['conditions'])


def growth_section(view):
    scaling=view['overview'].get('scaling',{})
    content = ['<p class="alert">固定工作量强扩展：线程数不是问题规模；区间是描述性统计，不能证明瓶颈根因。</p>' if scaling else '<p class="alert">经验增长模型只描述测量范围，不是 Big-O 数学证明。模型排序使用已保存的拟合结果。</p>']
    for variant,result in scaling.items():
        content.append('<h3>'+esc(variant)+' · 强扩展</h3>')
        content.append(table(['请求线程数','加速比','并行效率','加速比 95% 区间','测量筛查'],[
            [esc(p['workers']),number(p['speedup']),number(p['efficiency']),esc(p['speedup_interval_95_percent']),esc(p['measurement_status'])] for p in result['points']]))
        content.append(details('强扩展证据与限制',result))
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
                       '<div data-scale="linear">' + line_plot(series, '请求线程数' if scaling else '输入特征规模', unit) + '</div>' +
                       '<div data-scale="log" hidden>' + line_plot(series, '请求线程数' if scaling else '输入特征规模', unit, log=True) + '</div></div>')
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
    if view.get('lifecycle') is not None or view.get('candidate') is not None:
        sections.append(('lifecycle','实验进度与候选记录',lifecycle_section(view)))
    if view['view_kind'] in ('run', 'summary'):
        sections.extend([('metrics', '指标与样本', metrics_section(view)), ('hotspots', '采样热点', hotspot_section(view)),
                         ('reasoning', '事实与待验证假设', details('观测事实', view['facts']) + details('待验证假设', view['hypotheses']))])
    if view.get('cpu_selection') is not None:
        sections.append(('cpu-selection','CPU 样本筛选与分母',cpu_selection_section(view)))
    if view['sampling_profiles']:
        sections.append(('sampling','采样可解释性',sampling_section(view)))
    if view['runtime_identities']:
        sections.append(('runtime-identity','实际执行文件与加载映射',runtime_identity_section(view)))
    if view['phases']:
        sections.append(('phases','阶段与输入',phases_section(view)))
    if view['phase_summaries']:
        sections.append(('phase-summaries','映射后的阶段汇总',phase_summary_section(view)))
    if view['thread_timelines']:
        sections.append(('thread-activity','线程应用活动时间线',thread_activity_section(view)))
    if view.get('scheduler_timelines') or view.get('scheduler_queries'):
        sections.append(('scheduler','OS 线程切换时间线',scheduler_section(view)))
    if view['view_kind'] == 'study':
        if view['overview'].get('analysis')=='assessment':
            sections.append(('assessment','联合性能评估',assessment_section(view)))
            sections.append(('metrics','各输入和采集范围的指标',metrics_section(view)))
        elif view['overview'].get('analysis')=='cases':
            sections.append(('cases','文件样本实验',cases_section(view)))
            sections.append(('metrics','各输入的指标与样本',metrics_section(view)))
        else:sections.append(('paired','交错对照',paired_section(view)) if view['overview'].get('analysis')=='paired' else ('growth', '强扩展' if view['overview'].get('scaling') else '规模增长', growth_section(view)))
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
