"""Read, verify, and page archived OS context-switch evidence."""
import json
from pathlib import PurePosixPath

from .errors import LabError
from .protocol import positive, require, sha256
from .views import evidence_path


_STATES = ('Measured', 'Missing', 'Invalid')


def _inventory_entry(store, result, path):
    matches = [entry for entry in result.get('evidence', []) if entry['path'] == path]
    if len(matches) != 1:
        raise LabError('storage_failed', 'Scheduler evidence reference is absent or ambiguous in the run inventory',
                       {'path': path, 'matches': len(matches)})
    full_path, entry = evidence_path(store, result['run_id'], path)
    if sha256(full_path) != entry['sha256']:
        raise LabError('storage_failed', 'Scheduler evidence differs from its run inventory', {'path': path})
    return full_path, entry


def query_scheduler(store, identifier, stage, measurement, track=None, offset=0, limit=1000):
    require(stage in ('measurement', 'preflight'), 'Invalid scheduler execution stage')
    if stage == 'measurement':
        positive(measurement, 'measurement', integer=True, zero=True)
    else:
        require(measurement is None, 'Preflight has no formal measurement group')
    require(track is None or isinstance(track, str) and bool(track), 'track must be nonempty')
    positive(offset, 'offset', integer=True, zero=True)
    positive(limit, 'limit', integer=True)

    result = store.read(identifier)
    selection = dict(execution_stage=stage, measurement_group=measurement)
    timelines=result.get('scheduler_timelines', [])
    if not isinstance(timelines,list):
        raise LabError('storage_failed','Persisted scheduler timeline summary must be an array')
    if any(not isinstance(item,dict) for item in timelines):
        raise LabError('storage_failed','Persisted scheduler timeline entry must be an object')
    matching = [item for item in timelines
                if item['execution_stage'] == stage and item['measurement_group'] == measurement]
    if not matching:
        return dict(schema_version=1, run_id=identifier, run_status=result['status'],
            source_error=result['error'], execution_stage=stage, measurement_group=measurement,
            selection_status='INCONCLUSIVE', evidence_state='Missing', reason='No scheduler switch evidence was recorded for this launch group',
            selection=dict(track=track, offset=offset, limit=limit), profile=None, tracks=[], intervals=[],
            interval_page=dict(offset=offset, limit=limit, total=0, returned=0, truncated=False),
            limitations=['Missing scheduler evidence is not zero on/off-CPU time.'])
    if len(matching) != 1:
        raise LabError('storage_failed', 'One scheduler profile is required for the selected launch group')
    stored = matching[0]
    if stored['evidence_state'] not in _STATES:
        raise LabError('storage_failed', 'Invalid persisted scheduler evidence state')

    references = dict(profile=stored['evidence'], native=stored['native_evidence'],
                      events=stored['events_evidence'], intervals=stored['intervals_evidence'])
    files = {name: _inventory_entry(store, result, path)[0] for name, path in references.items()}
    try:
        profile = json.loads(files['profile'].read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError) as error:
        raise LabError('storage_failed', 'Cannot read stored scheduler profile', {'path': references['profile'], 'reason': str(error)}) from error
    if not (isinstance(profile, dict) and profile.get('schema_version') == 1 and
            profile.get('source') == 'perf-record-switch' and profile.get('evidence_state') == stored['evidence_state']):
        raise LabError('storage_failed', 'Stored scheduler profile does not match the run summary')
    parent = PurePosixPath(references['profile']).parent
    for key, name in (('native_evidence', 'native'), ('events_evidence', 'events'),
                      ('intervals_evidence', 'intervals')):
        if not (isinstance(profile.get(key), str) and
                str(parent / PurePosixPath(profile[key])) == references[name]):
            raise LabError('storage_failed', 'Scheduler evidence reference changed', {'field': key})
    tracks = profile['tracks']
    if not isinstance(tracks, list):
        raise LabError('storage_failed', 'Stored scheduler tracks must be an array')
    required_track_fields=('track_id','pid','tid','generation','switch_event_count','first_switch_ns',
        'last_switch_ns','on_cpu_seconds','off_cpu_seconds','closed_interval_count','left_censored',
        'right_censored','lifetime_complete')
    for row in tracks:
        valid_track=(isinstance(row,dict) and all(key in row for key in required_track_fields) and
            isinstance(row['track_id'],str) and all(type(row[key]) is int and row[key]>=0 for key in
            ('pid','tid','generation','switch_event_count','first_switch_ns','last_switch_ns','closed_interval_count')) and
            row['left_censored'] is True and row['right_censored'] is True and row['lifetime_complete'] is False)
        if not valid_track: raise LabError('storage_failed', 'Malformed persisted scheduler track')
    all_track_ids = {row['track_id'] for row in tracks}
    selected_tracks = [row for row in tracks if track is None or row['track_id'] == track]
    if track is not None:
        if not selected_tracks:
            raise LabError('invalid_request', 'Requested scheduler track does not exist', {'track': track})
    selected_ids = {row['track_id'] for row in selected_tracks}
    total = 0
    page = []
    try:
        with files['intervals'].open(encoding='utf-8') as source:
            for line_no, line in enumerate(source, 1):
                try:
                    row = json.loads(line)
                except ValueError as error:
                    raise LabError('storage_failed', 'Malformed stored scheduler interval',
                                   {'line': line_no, 'path': references['intervals']}) from error
                valid_interval=(isinstance(row, dict) and row.get('evidence_state') == 'Measured' and
                        row.get('state') in ('on_cpu', 'off_cpu') and row.get('track_id') in all_track_ids and
                        all(type(row.get(key)) is int and row[key] >= 0 for key in ('start_ns', 'end_ns', 'duration_ns')) and
                        row['end_ns'] >= row['start_ns'] and row['duration_ns'] == row['end_ns'] - row['start_ns'])
                if not valid_interval:raise LabError('storage_failed','Malformed scheduler interval evidence',{'line':line_no})
                if row['track_id'] in selected_ids:
                    if offset <= total < offset + limit:
                        page.append(row)
                    total += 1
    except OSError as error:
        raise LabError('storage_failed', 'Cannot read stored scheduler intervals',
                       {'path': references['intervals'], 'reason': str(error)}) from error

    state = stored['evidence_state']
    eligible = result['status'] == 'completed' and result['correctness']['status'] == 'passed'
    selection_status = 'PASS' if state == 'Measured' and total > 0 and eligible else 'INCONCLUSIVE'
    public_profile = {key: value for key, value in stored.items() if key not in ('intervals_evidence',)}
    return dict(schema_version=1, run_id=identifier, run_status=result['status'], source_error=result['error'],
        **selection, selection_status=selection_status, evidence_state=state,
        reason=profile.get('reason'), selection=dict(track=track, offset=offset, limit=limit),
        profile=public_profile, tracks=selected_tracks, intervals=page,
        correctness_status=result['correctness']['status'],
        interval_page=dict(offset=offset, limit=limit, total=total, returned=len(page),
                           truncated=offset + len(page) < total),
        references=references, limitations=profile['limitations'])
