"""Coordinated, explicitly competing two member paired measurements."""
import json
import multiprocessing
from multiprocessing.connection import wait
import os
from pathlib import Path
import signal
import time
import traceback

from .errors import LabError
from .storage import write_json, utc


def _worker(connection, inherited, request, store, context):
    """Set cancellation behavior before starting worker setup or lifecycle work."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    def terminate(signum, frame):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)

    try:
        _worker_body(connection, inherited, request, store, context)
    except KeyboardInterrupt:
        # Cancellation before lab.run owns a record has no run lifecycle to finalize.
        connection.close()


def _worker_body(connection, inherited, request, store, context):
    """Run the regular lifecycle in a forked process; parent owns study events."""
    for pipe in inherited:
        if pipe is not connection:
            pipe.close()

    class Hook:
        def __init__(self):
            self.context = dict(context)

        def _wait(self, expected):
            action = connection.recv()
            if action != expected:
                raise KeyboardInterrupt

        def ready(self, run_id, group, folder):
            connection.send(('READY', run_id, group, str(folder)))
            self._wait('GO')

        def started(self, pid, started_ns):
            connection.send(('FORMAL_STARTED', pid, started_ns, self.process_identity(pid)))

        def process_identity(self, pid):
            from .lifecycle import process_identity
            return process_identity(pid)

        def finished(self, execution_record):
            connection.send(('FINISHED', execution_record))
            if execution_record['status']!='completed' or execution_record['returncode']!=0:
                return
            message = connection.recv()
            if message[0] != 'POSTPROCESS':
                raise KeyboardInterrupt
            self.context.update(message[1])

    from .execution import _execution_events
    from .lifecycle import _record_created

    def created(kind, identifier):
        if kind != 'run':
            raise LabError('internal_error', 'Concurrent worker published a non-run record')
        connection.send(('CREATED', identifier))

    def execution_event(event, record):
        connection.send(('EXECUTION', event, record))

    created_token = _record_created.set(created)
    event_token = _execution_events.set(execution_event)
    try:
        if connection.recv() != 'PREPARE':
            raise KeyboardInterrupt
        from .lab import run
        result = run(request, store, measurement_hook=Hook())
        connection.send(('RESULT', result))
    except BaseException as error:
        connection.send(('ERROR', type(error).__name__, str(error), traceback.format_exc()))
    finally:
        _execution_events.reset(event_token)
        _record_created.reset(created_token)
        connection.close()


def _message(pipes, timeout, closed):
    deadline = time.monotonic() + timeout
    while True:
        available = [pipe for pipe in pipes if pipe not in closed]
        if not available:
            raise LabError('tool_failed', 'Both concurrent workers closed their event channels')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LabError('timeout', 'Concurrent wave synchronization timed out')
        ready = wait(available, remaining)
        if not ready:
            raise LabError('timeout', 'Concurrent wave synchronization timed out')
        for pipe in ready:
            try:
                return pipe, pipe.recv()
            except EOFError:
                closed.add(pipe)
                return pipe, ('EOF',)


def _write_wave(path, wave):
    Path(path).mkdir(parents=True, exist_ok=True)
    write_json(Path(path) / 'wave.json', wave)


def _result_error(result, message):
    raw_error = result.get('error')
    if raw_error is None:
        if result.get('status') == 'completed':
            return LabError('internal_error', 'Completed run was rejected as a failed result', {'run_id': result.get('run_id')})
        return LabError('internal_error', 'Noncompleted run result omitted its error record', {'run_id': result.get('run_id'), 'status': result.get('status')})
    category = raw_error['category']
    from .errors import EXIT_CODES
    if category not in EXIT_CODES:
        return LabError('internal_error', 'Run result contains an unknown error category', {'run_id': result.get('run_id'), 'run_error': raw_error})
    return LabError(category, message, {'run_id': result.get('run_id'), 'run_error': raw_error})


def _write_worker_error(directory, member, message):
    path = Path(directory) / ('worker-%s-%s-error.txt' % (member['slot'], member['variant']))
    path.write_text(message[3])
    return str(path.name)


def _runtime_summary(result):
    rows = []
    for identity in result.get('runtime_identities', []):
        if identity.get('execution_stage') != 'measurement' or identity.get('measurement_group') != 0:
            continue
        executable_rows = []
        for image in identity.get('actual_executables', []):
            if not image.get('target_match'):
                continue
            threads = image.get('thread_observation', {'evidence_state': 'Missing'})
            executable_rows.append({
                'pid': image['pid'], 'start_ticks': image['start_ticks'],
                'actual_executable': image['actual_executable'], 'target_match': image['target_match'],
                'evidence_state': threads.get('evidence_state', 'Missing'),
                'max_observed_count': threads.get('max_observed_count'),
                'observed_snapshots': threads.get('observed_snapshots', 0),
                'observations': [{'tid': row['tid'], 'allowed_cpus': row['allowed_cpus'],
                                  'observed_snapshots': row['observed_snapshots']} for row in threads.get('observations', [])],
                'evidence': {'identity': identity.get('evidence'), 'first_snapshot': threads.get('first_snapshot'),
                             'last_snapshot': threads.get('last_snapshot')},
            })
        rows.append({'run_id': result['run_id'], 'evidence': identity.get('evidence'),
                     'actual_executables': executable_rows})
    return rows if rows else {'evidence_state': 'Missing'}


def run_wave(members, store, directory, context, on_created):
    """Coordinate two `lab.run` lifecycles and preserve a wave-level audit record."""
    directory = Path(directory)
    conditions = context['resource_conditions']
    timeout = conditions['synchronization_timeout_seconds']
    wave = {'schema_version': 1, 'study_id': context['study_id'], 'block_id': context['block_id'],
            'wave_id': context['wave_id'], 'status': 'running', 'started_at': utc(),
            'execution_context': dict(context), 'scope': 'observed formal launcher-command lifetime after Popen through cleanup; includes tool and startup time, excludes postprocessing',
            'target_computation_overlap': {'evidence_state': 'Missing', 'reason': 'Launcher lifetime does not isolate target computation'},
            'members': [{'slot': m.get('slot'), 'variant': m.get('variant'),
                         'runtime_identities': {'evidence_state': 'Missing'},
                         'actual_executables': {'evidence_state': 'Missing'},
                         'thread_observation': {'evidence_state': 'Missing'}} for m in members],
            'overlap': None, 'overlap_fraction': None, 'error': None}
    _write_wave(directory, wave)

    def reject(category, message, details=None):
        error = LabError(category, message, details)
        wave['status'] = 'failed'; wave['ended_at'] = utc(); wave['error'] = error.as_dict()
        _write_wave(directory, wave)
        raise error

    if len(members) != 2:
        reject('invalid_request', 'Concurrent wave requires exactly two members')
    if os.name != 'posix' or not Path('/proc/self/task').is_dir():
        reject('unavailable', 'Concurrent wave requires Linux process observations')
    task_count = len(list(Path('/proc/self/task').iterdir()))
    if task_count != 1:
        reject('unavailable', 'Concurrent wave requires a single-threaded coordinator', {'parent_threads': task_count})
    ctx = multiprocessing.get_context('fork')
    parents, workers = [], []
    all_endpoints = []
    for member in members:
        parent, child = ctx.Pipe(duplex=True)
        parents.append(parent)
        workers.append(child)
        all_endpoints.extend((parent, child))
    processes = []
    runs = {}
    executions = [dict(starts=[], ends=[], owned={}) for _ in members]
    identities = {}
    active = set()
    self_stopping=set()
    closed = set()
    aborting = False
    error = None
    deadline = time.monotonic() + timeout
    try:
        for index, member in enumerate(members):
            worker_context = dict(context, slot=member['slot'], variant=member['variant'])
            proc = ctx.Process(target=_worker, args=(workers[index], all_endpoints, member['request'], store, worker_context))
            proc.start()
            processes.append(proc)
        for pipe in workers:
            pipe.close()
        parents[0].send('PREPARE')
        while not wave['members'][0].get('ready') or not wave['members'][1].get('ready'):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LabError('timeout', 'Concurrent wave did not reach measurement readiness')
            pipe, msg = _message(parents, remaining, closed)
            index = parents.index(pipe)
            tag = msg[0]
            if tag == 'CREATED':
                run_id = msg[1]
                runs[index] = run_id
                on_created(members[index]['slot'], members[index]['variant'], run_id)
            elif tag == 'READY':
                run_id = msg[1]
                if runs.get(index) != run_id:
                    raise LabError('internal_error', 'Worker readiness did not match its created run')
                if index == 1 and not wave['members'][0].get('ready'):
                    raise LabError('internal_error', 'Second worker became ready before the first READY')
                wave['members'][index].update(run_id=run_id, measurement_group=msg[2], folder=msg[3], ready=True)
                if index == 0:
                    parents[1].send('PREPARE')
            elif tag == 'EXECUTION':
                _, event, record = msg
                if event == 'start':
                    executions[index]['starts'].append(record)
                    executions[index]['owned'][record['pid']] = record
                else:
                    executions[index]['ends'].append(record)
                    executions[index]['owned'].pop(record['pid'], None)
            elif tag == 'ERROR':
                traceback_path = _write_worker_error(directory, members[index], msg)
                raise LabError('tool_failed', 'Concurrent worker failed before measurement readiness', {'worker': index, 'detail': msg[2], 'traceback_evidence': traceback_path})
            elif tag == 'RESULT':
                self_stopping.add(index)
                raise _result_error(msg[1], 'Concurrent worker finished before measurement readiness')
            elif tag == 'EOF':
                raise LabError('tool_failed', 'Concurrent worker channel closed before measurement readiness', {'worker': index})
        wave['release_monotonic_ns'] = time.monotonic_ns()
        for pipe in parents:
            pipe.send('GO')
        deadline = time.monotonic() + timeout
        while len([m for m in wave['members'] if m.get('formal_started')]) < 2:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LabError('timeout', 'Concurrent wave did not start both measurement launchers')
            pipe, msg = _message(parents, remaining, closed)
            index = parents.index(pipe)
            tag = msg[0]
            if tag == 'FORMAL_STARTED':
                _, pid, started_ns, identity = msg
                identities[index] = identity
                wave['members'][index].update(pid=pid, started_monotonic_ns=started_ns, process_identity=identity, formal_started=True)
            elif tag == 'EXECUTION':
                _, event, record = msg
                if event == 'start': executions[index]['owned'][record['pid']] = record; executions[index]['starts'].append(record)
                else: executions[index]['owned'].pop(record['pid'], None); executions[index]['ends'].append(record)
            elif tag == 'ERROR':
                traceback_path = _write_worker_error(directory, members[index], msg)
                raise LabError('tool_failed', 'Concurrent worker failed during measurement launch', {'worker': index, 'detail': msg[2], 'traceback_evidence': traceback_path})
            elif tag == 'RESULT':
                self_stopping.add(index)
                raise _result_error(msg[1], 'Concurrent worker completed before both measurements started')
            elif tag == 'FINISHED':
                raise LabError('incomparable', 'A measurement finished before both launchers started', {'worker': index, 'execution': msg[1]})
            elif tag == 'EOF':
                raise LabError('tool_failed', 'Concurrent worker channel closed before both launchers started', {'worker': index})
        from .lifecycle import observe_process
        observed_before=time.monotonic_ns()
        live = [observe_process(identities[i]) for i in range(2)]
        wave['simultaneous_liveness'] = {'observations': live, 'started_monotonic_ns':observed_before,
            'ended_monotonic_ns':time.monotonic_ns(), 'evidence_state': 'Measured' if all(x['alive'] is True for x in live) else 'Invalid'}
        if not all(x['alive'] is True for x in live):
            raise LabError('incomparable', 'Both measurement leaders were not observed alive simultaneously')
        deadline = time.monotonic() + timeout
        finished = {}
        while len(finished) < 2:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LabError('timeout', 'Concurrent measurement completion barrier timed out')
            pipe, msg = _message(parents, remaining, closed)
            index = parents.index(pipe)
            tag = msg[0]
            if tag == 'FINISHED':
                finished[index] = msg[1]
                wave['members'][index]['execution'] = msg[1]
                if msg[1]['status'] != 'completed' or msg[1]['returncode'] != 0:
                    self_stopping.add(index)
                    status = msg[1]['status']
                    category = status if status in ('timeout', 'cancelled') else 'tool_failed'
                    raise LabError(category, 'Concurrent measurement failed; peer cancellation started', {'worker': index, 'execution': msg[1]})
            elif tag == 'EXECUTION':
                _, event, record = msg
                if event == 'start': executions[index]['owned'][record['pid']] = record; executions[index]['starts'].append(record)
                else: executions[index]['owned'].pop(record['pid'], None); executions[index]['ends'].append(record)
            elif tag == 'ERROR':
                traceback_path = _write_worker_error(directory, members[index], msg)
                raise LabError('tool_failed', 'Concurrent worker failed during measurement', {'worker': index, 'detail': msg[2], 'traceback_evidence': traceback_path})
            elif tag == 'RESULT':
                raise LabError('tool_failed', 'Worker completed without the measurement barrier', {'worker': index})
            elif tag == 'EOF':
                raise LabError('tool_failed', 'Concurrent worker channel closed before measurement finished', {'worker': index})
        a, b = [finished[i] for i in range(2)]
        start = max(a['started_monotonic_ns'], b['started_monotonic_ns'])
        end = min(a['ended_monotonic_ns'], b['ended_monotonic_ns'])
        overlap_ns = max(0, end - start)
        denominator = min(a['ended_monotonic_ns'] - a['started_monotonic_ns'], b['ended_monotonic_ns'] - b['started_monotonic_ns'])
        if denominator <= 0:
            raise LabError('incomparable', 'A zero-duration launcher interval cannot establish overlap')
        fraction = overlap_ns / denominator
        wave.update(status='PASS', overlap={'nanoseconds': overlap_ns, 'start_difference_nanoseconds': abs(a['started_monotonic_ns'] - b['started_monotonic_ns']),
                             'evidence_state': 'Measured'}, overlap_fraction=fraction)
        wave['event_provenance'] = {'source': 'execution.execute start/end hooks', 'clock': 'CLOCK_MONOTONIC'}
        if any(record['status'] != 'completed' or record['returncode'] != 0 for record in finished.values()):
            raise LabError('tool_failed', 'Concurrent measurement launcher did not complete successfully')
        if overlap_ns <= 0 or fraction < conditions['minimum_overlap_fraction']:
            raise LabError('incomparable', 'Observed measurement overlap is below the preregistered minimum', {'overlap_fraction': fraction})
        post = {'status': 'PASS', 'overlap_fraction': fraction, 'overlap': wave['overlap']}
        for pipe in parents:
            pipe.send(('POSTPROCESS', post))
        deadline = time.monotonic() + timeout
        while len(active) < 2:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LabError('timeout', 'Concurrent postprocessing timed out')
            pipe, msg = _message(parents, remaining, closed)
            index = parents.index(pipe)
            if msg[0] == 'RESULT':
                runs[index] = msg[1]['run_id']
                if msg[1]['status']!='completed':
                    self_stopping.add(index)
                    raise _result_error(msg[1],'Concurrent postprocessing or validation failed')
                active.add(index)
                wave['members'][index]['status'] = msg[1]['status']
                wave['members'][index]['run_id'] = msg[1]['run_id']
                wave['members'][index]['execution_context'] = msg[1].get('execution_context')
                wave['members'][index]['result_status'] = msg[1]['status']
                wave['members'][index]['_result'] = msg[1]
            elif msg[0] == 'EXECUTION':
                _, event, record = msg
                if event == 'start': executions[index]['owned'][record['pid']] = record
                else: executions[index]['owned'].pop(record['pid'], None)
            elif msg[0] == 'ERROR':
                traceback_path = _write_worker_error(directory, members[index], msg)
                raise LabError('tool_failed', 'Concurrent worker failed during postprocessing', {'worker': index, 'detail': msg[2], 'traceback_evidence': traceback_path})
            elif msg[0] == 'EOF' and index not in active:
                raise LabError('tool_failed', 'Concurrent worker channel closed before its run result', {'worker': index})
        results = [wave['members'][i].pop('_result') for i in range(2)]
        wave['status'] = 'PASS' if all(r['status'] == 'completed' for r in results) else 'failed'
        wave['members'] = [{k: v for k, v in m.items() if k != 'ready'} for m in wave['members']]
        for index, result in enumerate(results):
            runtime = _runtime_summary(result)
            executables = [image for row in runtime for image in row['actual_executables']] if isinstance(runtime, list) else []
            wave['members'][index].update(
                runtime_identities=runtime,
                actual_executables=executables if executables else {'evidence_state': 'Missing'},
                thread_observation={'evidence_state': ('Measured' if any(image['evidence_state']=='Measured' for image in executables)
                    else 'Unsupported' if any(image['evidence_state']=='Unsupported' for image in executables) else 'Missing'), 'per_pid': [
                    {'pid': image['pid'], 'start_ticks': image['start_ticks'],
                     'evidence_state': image['evidence_state'], 'max_observed_count': image['max_observed_count'],
                     'observed_snapshots': image['observed_snapshots'], 'observations': image['observations'],
                     'evidence': image['evidence']} for image in executables]} if executables else {'evidence_state': 'Missing'})
        wave['ended_at'] = utc()
        if wave['status'] != 'PASS':
            failure = next(result for result in results if result['status'] != 'completed')
            failure_error = _result_error(failure, 'Concurrent paired member failed after measurement')
            raise failure_error
        for proc in processes:
            proc.join()
        wave['worker_exitcodes']=[proc.exitcode for proc in processes]
        _write_wave(directory,wave)
        return results, wave
    except BaseException as caught:
        error = caught
        if isinstance(caught, KeyboardInterrupt):
            error = LabError('cancelled', 'Concurrent wave interrupted by the caller')
        if not aborting:
            aborting = True
            for index, proc in enumerate(processes):
                if index not in self_stopping and proc.is_alive():
                    try: os.kill(proc.pid, signal.SIGTERM)
                    except ProcessLookupError: pass
        cleanup_deadline = time.monotonic() + 10
        while time.monotonic() < cleanup_deadline:
            got_message = False
            for index, pipe in enumerate(parents):
                while pipe not in closed and pipe.poll():
                    got_message = True
                    try: msg = pipe.recv()
                    except EOFError:
                        closed.add(pipe)
                        break
                    if msg[0] == 'CREATED':
                        run_id = msg[1]
                        if index not in runs:
                            runs[index] = run_id
                            on_created(members[index]['slot'], members[index]['variant'], run_id)
                        elif runs[index] != run_id:
                            raise LabError('internal_error', 'Worker published multiple run records')
                    elif msg[0] == 'EXECUTION':
                        _, event, record = msg
                        if event == 'start': executions[index]['owned'][record['pid']] = record
                        else: executions[index]['owned'].pop(record['pid'], None)
                    elif msg[0] == 'RESULT':
                        runs[index] = msg[1]['run_id']
                    elif msg[0] == 'ERROR':
                        _write_worker_error(directory, members[index], msg)
            for proc in processes:
                proc.join(0)
            if all(not proc.is_alive() for proc in processes) and not got_message:
                break
            if not got_message:
                time.sleep(0.01)
        for proc in processes:
            if proc.is_alive():
                proc.kill()
            proc.join()
        for index, pipe in enumerate(parents):
            while pipe not in closed and pipe.poll():
                try: msg = pipe.recv()
                except EOFError:
                    closed.add(pipe)
                    break
                if msg[0] == 'CREATED':
                    run_id = msg[1]
                    if index not in runs:
                        runs[index] = run_id
                        on_created(members[index]['slot'], members[index]['variant'], run_id)
                    elif runs[index] != run_id:
                        raise LabError('internal_error', 'Worker published multiple run records')
                elif msg[0] == 'EXECUTION':
                    _, event, record = msg
                    if event == 'start': executions[index]['owned'][record['pid']] = record
                    else: executions[index]['owned'].pop(record['pid'], None)
                elif msg[0] == 'RESULT':
                    runs[index] = msg[1]['run_id']
                elif msg[0] == 'ERROR':
                    _write_worker_error(directory, members[index], msg)
        for record in (r for state in executions for r in state['owned'].values()):
            try: os.killpg(record['pid'], signal.SIGTERM)
            except ProcessLookupError: pass
        time.sleep(0.3)
        for record in (r for state in executions for r in state['owned'].values()):
            try: os.killpg(record['pid'], signal.SIGKILL)
            except ProcessLookupError: pass
        for index, run_id in list(runs.items()):
            try:
                measured = store.read(run_id)
                if measured['status'] == 'running':
                    measured['status'] = 'failed'
                    measured['error'] = LabError('tool_failed', 'Concurrent worker exited before completing its run record', {'worker_exitcode': processes[index].exitcode}).as_dict()
                    measured['ended_at'] = utc()
                    from .storage import inventory
                    measured['evidence']=inventory(store.record_path(run_id).parent)
                    write_json(store.record_path(run_id), measured)
            except LabError as read_error:
                if read_error.category != 'not_found':
                    raise
                wave.setdefault('run_record_errors', []).append({'run_id': run_id, 'error': read_error.as_dict()})
        wave['status'] = 'cancelled' if isinstance(error,LabError) and error.category=='cancelled' else 'failed'
        wave['ended_at'] = utc()
        wave['error'] = error.as_dict() if isinstance(error, LabError) else {'category': 'internal_error', 'message': str(error)}
        wave['owned_process_groups'] = [rec for state in executions for rec in state['owned'].values()]
        wave['worker_exitcodes'] = [proc.exitcode for proc in processes]
        _write_wave(directory, wave)
        if isinstance(error, LabError):
            raise LabError(error.category,str(error),{**error.details,'wave':wave}) from caught
        raise LabError('internal_error', 'Concurrent wave failed', {'reason': str(error), 'wave': wave}) from caught
    finally:
        for pipe in parents + workers:
            if not pipe.closed:
                pipe.close()
