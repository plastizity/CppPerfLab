"""Normalize perf's recorded per-task switch events into censored CPU intervals."""
import json
import re
from pathlib import Path

from .errors import LabError
from .storage import write_json


_HEADER = re.compile(r'^\s*(\d+)/(\d+)\s+(\d+)\.(\d{9}):\s*(.*?)\s*$')
_SWITCH = re.compile(r'^PERF_RECORD_SWITCH\s+(IN|OUT)(?:\s+(preempt))?\s*$')
_FORK_EXIT = re.compile(r'^PERF_RECORD_(FORK|EXIT)\((\d+):(\d+)\):\((\d+):(\d+)\)\s*$')
_LOST = re.compile(r'^PERF_RECORD_LOST(?:_SAMPLES)?\s+lost\s+(\d+)(?:\s+events?)?\s*$')
_SAMPLE = re.compile(r'^(?:\d+\s+)?cpu-clock(?::[A-Za-z0-9_-]+)?:\s*$')


_LIMITATIONS = [
    'Only intervals with two recorded context-switch boundaries are measured; capture-boundary time is censored.',
    'PERF_RECORD_SWITCH does not report wakeups, runqueue delay, blocking cause or lock contention.',
    'Time outside sampled threads is not measured; per-thread intervals cannot be summed as process utilization.',
    'PERF_RECORD_EXIT and capture end never synthesize a missing switch boundary.',
]


def _timestamp_ns(whole, fraction):
    return int(whole) * 1_000_000_000 + int(fraction)


def _context(directory):
    attributes = (directory / 'sample-attributes' / 'stdout.txt').read_text()
    if not re.search(r'\buse_clockid:\s*1\b', attributes) or not re.search(r'\bclockid:\s*1\b', attributes):
        raise LabError('parse_failed', 'Recorded perf attributes do not verify CLOCK_MONOTONIC')
    if not re.search(r'\bcontext_switch:\s*1\b', attributes):
        raise LabError('parse_failed', 'Recorded perf attributes do not verify context-switch records')
    profile = json.loads((directory / 'sampling-profile.json').read_text())
    clock = profile['clock']
    required = ('name', 'boot_id', 'pid_namespace', 'time_namespace', 'monotonic_offset_ns',
                'before_launch_monotonic_ns', 'after_capture_monotonic_ns')
    if any(key not in clock for key in required) or clock['name'] != 'CLOCK_MONOTONIC' or clock['monotonic_offset_ns'] != 0:
        raise LabError('parse_failed', 'Stored perf sampling clock is incomplete or unverified')
    return {key: clock[key] for key in required}


def _write_jsonl(stream, value):
    stream.write(json.dumps(value, sort_keys=True, allow_nan=False) + '\n')


def normalize_switches(directory):
    """Archive raw switch-derived events and pair only complete per-thread boundaries."""
    directory = Path(directory)
    clock = _context(directory)
    native_path = directory / 'switch-script' / 'stdout.txt'
    events_path = directory / 'scheduler-events.jsonl'
    intervals_path = directory / 'scheduler-intervals.jsonl'

    issues = []
    loss_record_count = 0
    switch_event_count = 0
    tracks = {}
    generations = {}
    active_lifetimes = set()
    seen_lifetimes = set()

    def issue(code, line, reason):
        issues.append(dict(code=code, line=line, reason=reason))

    def check_capture_bounds(timestamp, line_no):
        if not clock['before_launch_monotonic_ns'] <= timestamp <= clock['after_capture_monotonic_ns']:
            raise LabError('parse_failed', 'Scheduler event timestamp is outside the verified perf capture',
                           {'line': line_no, 'timestamp_ns': timestamp})

    def track_for(pid, tid, line_no, timestamp):
        key = (pid, tid)
        generation = generations.get(key, 0)
        identity = (pid, tid, generation)
        name = '%d:%d:%d' % identity
        if name not in tracks:
            tracks[name] = dict(track_id=name, pid=pid, tid=tid, generation=generation,
                switch_event_count=0, first_switch_ns=timestamp, last_switch_ns=timestamp,
                on_cpu_seconds=None, off_cpu_seconds=None, closed_interval_count=0,
                left_censored=True, right_censored=True, lifetime_complete=False)
        return identity, name

    # First pass writes normalized source events and validates per-thread transitions.
    with native_path.open(encoding='utf-8') as source, events_path.open('x', encoding='utf-8') as event_output:
        for line_no, line in enumerate(source, 1):
            text = line.rstrip('\n')
            if not text.strip() or text.lstrip().startswith('#'):
                continue
            match = _HEADER.fullmatch(text)
            if match is None:
                raise LabError('parse_failed', 'Malformed perf switch-script record', {'line': line_no, 'text': text})
            row_pid, row_tid = int(match[1]), int(match[2])
            timestamp = _timestamp_ns(match[3], match[4])
            body = match[5]
            if body.startswith('PERF_RECORD_COMM') or body.startswith('PERF_RECORD_MMAP'):
                continue
            sample = _SAMPLE.fullmatch(body)
            if sample:
                continue
            if body.startswith('PERF_RECORD_SWITCH_CPU_WIDE'):
                raise LabError('unavailable', 'CPU-wide switch records lack a supported subject-TID mapping',
                               {'line': line_no, 'record': body})
            event = dict(line=line_no, timestamp_ns=timestamp, row_pid=row_pid, row_tid=row_tid)
            switch = _SWITCH.fullmatch(body)
            lifecycle = _FORK_EXIT.fullmatch(body)
            lost = _LOST.fullmatch(body)
            if switch:
                check_capture_bounds(timestamp, line_no)
                if row_pid <= 0 or row_tid <= 0:
                    raise LabError('parse_failed', 'Switch event has an invalid task identity', {'line': line_no})
                direction = switch[1]
                if switch[2] and direction != 'OUT':
                    raise LabError('parse_failed', 'Only switch-out records may carry the preempt flag', {'line': line_no})
                identity, track_id = track_for(row_pid, row_tid, line_no, timestamp)
                active_lifetimes.add((row_pid,row_tid))
                seen_lifetimes.add(identity)
                track = tracks[track_id]
                track['switch_event_count'] += 1
                track['last_switch_ns'] = timestamp
                switch_event_count += 1
                event.update(kind='switch', pid=row_pid, tid=row_tid, generation=identity[2], track_id=track_id,
                             direction=direction, flags=['preempted'] if switch[2] else [])
                previous = track.pop('_pending_switch', None)
                if previous is not None:
                    if previous['direction'] == direction:
                        issue('repeated_direction', line_no, 'Repeated %s boundary before an opposite switch' % direction)
                    elif timestamp < previous['timestamp_ns']:
                        issue('out_of_order', line_no, 'Switch timestamp precedes the previous thread boundary')
                track['_pending_switch'] = dict(direction=direction, timestamp_ns=timestamp, line=line_no)
                _write_jsonl(event_output, event)
                continue
            if lifecycle:
                check_capture_bounds(timestamp, line_no)
                kind = lifecycle[1].lower()
                pid, tid, parent_pid, parent_tid = map(int, lifecycle.groups()[1:])
                if min(pid, tid, parent_pid, parent_tid) <= 0:
                    raise LabError('parse_failed', 'Fork/exit event has an invalid task identity', {'line': line_no})
                key = (pid, tid)
                if kind == 'fork':
                    if key in active_lifetimes:
                        issue('duplicate_fork', line_no, 'Task lifetime was forked twice without a recorded exit')
                    generation = generations.get(key, 0)
                    life = (pid, tid, generation)
                    if life in seen_lifetimes:
                        issue('duplicate_lifetime', line_no, 'Fork reuses an already observed task generation')
                    active_lifetimes.add(key)
                    seen_lifetimes.add(life)
                else:
                    active_lifetimes.discard(key)
                    track_id = '%d:%d:%d' % (pid, tid, generations.get(key, 0))
                    track = tracks.get(track_id)
                    if track is not None:
                        track.pop('_pending_switch', None)
                    generations[key] = generations.get(key, 0) + 1
                event.update(kind=kind, pid=pid, tid=tid, parent_pid=parent_pid, parent_tid=parent_tid)
                _write_jsonl(event_output, event)
                continue
            if body.startswith(('PERF_RECORD_LOST ', 'PERF_RECORD_LOST_SAMPLES ')):
                check_capture_bounds(timestamp, line_no)
                if lost is None or int(lost[1]) <= 0:
                    raise LabError('parse_failed', 'Malformed perf lost-event record', {'line': line_no, 'text': text})
                loss_record_count += 1
                issue('lost_events', line_no, 'perf reported %d lost records' % int(lost[1]))
                event.update(kind='lost', lost_count=int(lost[1]))
                _write_jsonl(event_output, event)
                continue
            raise LabError('parse_failed', 'Unsupported perf switch-script record', {'line': line_no, 'text': text})

    invalid = bool(issues)
    if invalid:
        for track in tracks.values():
            track['on_cpu_seconds'] = None
            track['off_cpu_seconds'] = None
            track['closed_interval_count'] = 0
        intervals_path.open('x', encoding='utf-8').close()
        evidence_state = 'Invalid'
    else:
        # The interval file is written once, only after the full event stream validated.
        pending = {}
        totals = {}
        with events_path.open(encoding='utf-8') as events, intervals_path.open('x', encoding='utf-8') as interval_output:
            for encoded in events:
                event = json.loads(encoded)
                if event['kind'] != 'switch':
                    continue
                track_id = event['track_id']
                previous = pending.get(track_id)
                if previous is not None:
                    state = 'on_cpu' if previous['direction'] == 'IN' else 'off_cpu'
                    duration_ns = event['timestamp_ns'] - previous['timestamp_ns']
                    interval = dict(track_id=track_id, pid=event['pid'], tid=event['tid'],
                        generation=event['generation'], state=state, start_ns=previous['timestamp_ns'],
                        end_ns=event['timestamp_ns'], duration_ns=duration_ns,
                        start_line=previous['line'], end_line=event['line'], evidence_state='Measured')
                    _write_jsonl(interval_output, interval)
                    values = totals.setdefault(track_id, {})
                    values[state] = values.get(state, 0) + duration_ns
                    tracks[track_id]['closed_interval_count'] += 1
                pending[track_id] = dict(direction=event['direction'],
                    timestamp_ns=event['timestamp_ns'], line=event['line'])
        for track_id, track in tracks.items():
            track.pop('_pending_switch', None)
            values = totals.get(track_id, {})
            track['on_cpu_seconds'] = values['on_cpu'] / 1e9 if 'on_cpu' in values else None
            track['off_cpu_seconds'] = values['off_cpu'] / 1e9 if 'off_cpu' in values else None
        evidence_state = 'Measured' if totals else 'Missing'

    if not invalid and evidence_state == 'Missing':
        reason='No closed switch intervals were captured; incomplete boundaries are not zero time'
    else:
        reason=None
    for track in tracks.values():
        track.pop('_pending_switch', None)
    profile=dict(schema_version=1,source='perf-record-switch',evidence_state=evidence_state,clock=clock,
        native_evidence='switch-script/stdout.txt',events_evidence='scheduler-events.jsonl',
        intervals_evidence='scheduler-intervals.jsonl',switch_event_count=switch_event_count,
        loss_record_count=loss_record_count,issues=issues,tracks=list(tracks.values()),limitations=list(_LIMITATIONS))
    if reason:profile['reason']=reason
    write_json(directory/'scheduler-profile.json',profile)
    return profile
