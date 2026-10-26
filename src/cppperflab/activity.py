"""Analysis of explicitly annotated application wall-activity intervals."""
import json
from collections import defaultdict

from .errors import LabError
from .metrics import observation
from .protocol import metric


METRIC_IDS = (
    'phase.application_wait_wall',
    'phase.application_work_wall',
    'phase.application_unclassified_wall',
    'phase.application_observed_wall',
    'phase.application_wait_fraction',
)

_SCHEDULER_LIMITATION = (
    'No OS scheduling trace collected; application wall scopes are not off-CPU '
    'or exact running time'
)


def _merge_ns(intervals):
    merged = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        elif end > merged[-1][1]:
            merged[-1][1] = end
    return merged


def _correctness(events):
    states = {event['correctness'] for event in events}
    if 'failed' in states:
        return 'failed'
    if states and states == {'passed'}:
        return 'passed'
    return 'not_checked'


def _track_identity(clock, track_id):
    actor = clock['tracks'].get(track_id) if clock is not None else None
    if actor is None:
        return None, None, 'Missing'
    return actor['pid'], actor['tid'], 'Measured'


def _analyze_track(track_id, events, clock):
    roots = _merge_ns((event['start_ns'], event['end_ns']) for event in events
                      if event['parent_id'] is None)
    root_boundaries = defaultdict(int)
    for start, end in roots:
        root_boundaries[start] += 1
        root_boundaries[end] -= 1

    markers = []
    definitions = set()
    declared_work = declared_wait = False
    for event in events:
        activity = event.get('activity')
        if activity is None:
            continue
        kind = activity['kind']
        reason = activity.get('reason') if kind == 'application_wait' else None
        markers.append((event['start_ns'], event['end_ns'], kind, reason, event['id']))
        definitions.add((tuple(event['path']), kind, reason))
        declared_work |= kind == 'application_work'
        declared_wait |= kind == 'application_wait'

    # A boundary sweep unions identical annotations while detecting overlapping
    # incompatible declarations. Root counts independently define observation.
    changes = defaultdict(list)
    for start, end, kind, reason, phase_id in markers:
        if start == end:
            continue
        label = (kind, reason)
        changes[start].append((1, label, phase_id))
        changes[end].append((-1, label, phase_id))
    boundaries = sorted(set(root_boundaries) | set(changes))
    active = defaultdict(set)
    in_roots = 0
    intervals = []
    totals = defaultdict(int)
    wait_reasons = defaultdict(int)

    for index, point in enumerate(boundaries):
        for delta, label, phase_id in changes.get(point, ()):
            if delta < 0:
                active[label].remove(phase_id)
                if not active[label]:
                    del active[label]
        in_roots += root_boundaries.get(point, 0)
        for delta, label, phase_id in changes.get(point, ()):
            if delta > 0:
                active[label].add(phase_id)
        if index + 1 == len(boundaries):
            continue
        next_point = boundaries[index + 1]
        if next_point <= point or in_roots <= 0:
            continue
        labels = [label for label, ids in active.items() if ids]
        if len(labels) > 1:
            raise LabError('parse_failed', 'Conflicting application activity annotations overlap',
                           {'track_id': track_id, 'start_ns': point, 'end_ns': next_point,
                            'annotations': sorted((kind, reason) for kind, reason in labels)})
        label = labels[0] if labels else ('unclassified', None)
        kind, reason = label
        phase_ids = sorted(phase_id for ids in active.values() for phase_id in ids)
        duration = next_point - point
        totals[kind] += duration
        if kind == 'application_wait':
            wait_reasons[reason] += duration
        interval = dict(start_ns=point, end_ns=next_point, kind=kind,
                        reason=reason, phase_ids=phase_ids)
        if intervals and intervals[-1]['end_ns'] == point and all(
                intervals[-1][key] == interval[key]
                for key in ('kind', 'reason', 'phase_ids')):
            intervals[-1]['end_ns'] = next_point
        else:
            intervals.append(interval)

    observed_ns = sum(end - start for start, end in roots)
    wait_ns = totals['application_wait']
    work_ns = totals['application_work']
    unclassified_ns = totals['unclassified']
    coverage = ((wait_ns + work_ns) / observed_ns) if observed_ns else None
    wait_fraction = (wait_ns / observed_ns) if declared_wait and observed_ns else None
    pid, tid, identity_state = _track_identity(clock, track_id)
    return dict(
        track_id=track_id, pid=pid, tid=tid, identity_state=identity_state,
        observed_seconds=observed_ns / 1e9,
        work_seconds=work_ns / 1e9 if declared_work else None,
        wait_seconds=wait_ns / 1e9 if declared_wait else None,
        unclassified_seconds=unclassified_ns / 1e9,
        wait_fraction=wait_fraction,
        annotation_coverage_fraction=coverage,
        wait_reasons=[dict(reason=reason, seconds=duration / 1e9)
                      for reason, duration in sorted(wait_reasons.items())],
        intervals=intervals,
        declared_work=declared_work, declared_wait=declared_wait,
        correctness_status=_correctness(events),
        counter_contract=json.dumps(sorted(definitions), ensure_ascii=False,
                                    separators=(',', ':')))


def analyze(events, scope, clock, evidence):
    """Build per-thread application activity intervals from normalized phases."""
    grouped = defaultdict(list)
    for event in events:
        grouped[event['track_id']].append(event)

    tracks = []
    metrics = []
    unavailable = []
    any_explicit = False
    for track_id, group in sorted(grouped.items()):
        track = _analyze_track(track_id, group, clock)
        any_explicit |= track['declared_work'] or track['declared_wait']
        metric_scope = scope + '; thread_track=' + json.dumps(
            track_id, ensure_ascii=False, separators=(',', ':'))
        common = dict(method='target-activity-intervals', scope=metric_scope,
                      evidence=evidence, correctness_status=track['correctness_status'],
                      counter_contract=track['counter_contract'], category='Diagnostic',
                      expected_direction='none')
        numeric = (
            ('phase.application_wait_wall', track['wait_seconds'], 'seconds',
             track['declared_wait'], 'No application_wait annotation on this track'),
            ('phase.application_work_wall', track['work_seconds'], 'seconds',
             track['declared_work'], 'No application_work annotation on this track'),
            ('phase.application_unclassified_wall', track['unclassified_seconds'], 'seconds', True, None),
            ('phase.application_observed_wall', track['observed_seconds'], 'seconds', True, None),
            ('phase.application_wait_fraction', track['wait_fraction'], 'ratio',
             track['declared_wait'] and track['observed_seconds'] > 0,
             'Wait fraction requires a wait annotation and nonzero observed wall time'),
        )
        for name, value, unit, available, reason in numeric:
            if available:
                metrics.append(metric(name, value, unit, **common))
            else:
                unavailable.append(observation(name, 'Missing', reason=reason,
                                               scope=metric_scope, unit=unit,
                                               method='target-activity-intervals',
                                               evidence=evidence,
                                               correctness_status=track['correctness_status'],
                                               counter_contract=track['counter_contract']))
        for key in ('declared_work', 'declared_wait', 'counter_contract'):
            del track[key]
        tracks.append(track)

    return dict(timeline=dict(
        schema_version=1, scope=scope,
        evidence_state='Measured' if any_explicit else 'Missing', evidence=evidence,
        tracks=tracks,
        os_scheduler=dict(evidence_state='Missing', reason=_SCHEDULER_LIMITATION),
        limitations=[_SCHEDULER_LIMITATION,
                     'Application activity is target-declared wall time, not exact CPU running or off-CPU time.']),
        metrics=metrics, unavailable_observations=unavailable)
