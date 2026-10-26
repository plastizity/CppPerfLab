"""Target-owned structured phase events; interval arithmetic, not a trace collector."""
import json
from collections import defaultdict
from pathlib import Path

from .errors import LabError
from .protocol import fields, require, positive, metric
from .metrics import observation
from .activity import METRIC_IDS as ACTIVITY_METRICS

METRIC_IDS=('phase.inclusive_time','phase.self_time','phase.calls','phase.work',
            'phase.covered_wall','phase.uncovered_wall','phase.root_wall')+ACTIVITY_METRICS


def union_ns(intervals):
    end=None;total=0
    for lo,hi in sorted(intervals):
        total+=hi-max(lo,end if end is not None else lo) if end is None or hi>end else 0
        end=hi if end is None else max(end,hi)
    return total


def normalize(path):
    try:
        raw=json.loads(Path(path).read_text(),parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))
        if isinstance(raw,dict) and raw.get('representation')=='summary':
            from .phase_summaries import normalize_summary
            return normalize_summary(raw,path)
        fields(raw,('schema_version','scope','start_ns','end_ns','stages','clock'),('schema_version','scope','start_ns','end_ns','stages'),'phase report')
        require(type(raw['schema_version']) is int and raw['schema_version']==1,'Unsupported phase report version')
        require(isinstance(raw['scope'],str) and bool(raw['scope']),'Phase root scope required')
        for k in ('start_ns','end_ns'):positive(raw[k],k,integer=True,zero=True)
        require(raw['end_ns']>=raw['start_ns'],'Reversed root interval')
        require(isinstance(raw['stages'],list),'stages must be an array')
        clock=raw.get('clock')
        if clock is not None:
            fields(clock,('name','origin_ns','tracks','boot_id','pid_namespace','time_namespace'),('name','origin_ns','tracks','boot_id','pid_namespace','time_namespace'),'phase clock')
            require(clock['name']=='CLOCK_MONOTONIC','Only explicit Linux CLOCK_MONOTONIC can be correlated')
            positive(clock['origin_ns'],'origin_ns',integer=True,zero=True)
            require(all(isinstance(clock[k],str) and bool(clock[k]) for k in ('boot_id','pid_namespace','time_namespace')),'Clock namespace identity required')
            require(isinstance(clock['tracks'],dict),'Clock tracks must be an object')
            for track,actor in clock['tracks'].items():
                require(isinstance(track,str) and bool(track),'Clock track ID required')
                fields(actor,('pid','tid'),('pid','tid'),'phase clock actor')
                for k in ('pid','tid'):positive(actor[k],k,integer=True)
        rows={};children=defaultdict(list)
        required=('id','name','parent_id','track_id','call_id','input_id','start_ns','end_ns','children_complete','result','correctness','work')
        for row in raw['stages']:
            fields(row,required+('activity',),required,'phase event')
            if 'activity' in row:
                activity=row['activity']
                fields(activity,('kind','reason'),('kind',),'phase activity')
                require(activity['kind'] in ('application_work','application_wait'),'Unknown application activity kind')
                if activity['kind']=='application_wait':
                    require(isinstance(activity.get('reason'),str) and bool(activity['reason'].strip()),'Application wait needs an explicit caller reason')
                else:require('reason' not in activity,'Application work does not have a wait reason')
            require(all(isinstance(row[k],str) and bool(row[k]) for k in ('id','name','track_id','call_id','result')),'Invalid phase identity')
            require(isinstance(row['input_id'],str),'input_id must be a string; empty means unknown')
            require(row['parent_id'] is None or isinstance(row['parent_id'],str),'Invalid parent_id')
            require(row['id'] not in rows,'Duplicate phase event ID')
            require(type(row['children_complete']) is bool,'children_complete must be explicit')
            require(row['correctness'] in ('passed','failed','not_checked'),'Invalid phase correctness')
            for k in ('start_ns','end_ns'):positive(row[k],k,integer=True,zero=True)
            require(raw['start_ns']<=row['start_ns']<=row['end_ns']<=raw['end_ns'],'Phase outside root interval')
            require(isinstance(row['work'],dict),'work must be an object')
            for name,value in row['work'].items():
                require(isinstance(name,str) and bool(name),'Empty work counter name')
                fields(value,('value','unit','evidence_state'),('value','unit','evidence_state'),'work counter')
                require(value['evidence_state'] in ('Measured','Estimated','Modeled'),'Invalid work evidence state')
                positive(value['value'],'work count',zero=True)
                require(isinstance(value['unit'],str) and bool(value['unit']),'Work unit required')
            if clock is not None:require(row['track_id'] in clock['tracks'],'Phase clock actor missing for track')
            rows[row['id']]=row
            children[row['parent_id']].append(row)
        paths={}
        for event in rows.values():
            trail=[];seen=set();current=event
            while current['id'] not in paths:
                require(current['id'] not in seen,'Cyclic phase ancestry')
                seen.add(current['id']);trail.append(current)
                parent=current['parent_id']
                if parent is None:break
                require(parent in rows,'Missing phase parent')
                owner=rows[parent]
                require(owner['track_id']==current['track_id'],'Cross-track parentage unsupported; use separate root events')
                require(owner['start_ns']<=current['start_ns']<=current['end_ns']<=owner['end_ns'],'Child outside parent interval')
                current=owner
            prefix=paths.get(current['id'],())
            for item in reversed(trail):
                prefix=prefix+(item['name'],);paths[item['id']]=prefix
        groups=defaultdict(list);events=[]
        for event in rows.values():
            inclusive=event['end_ns']-event['start_ns']
            residue=inclusive-union_ns((c['start_ns'],c['end_ns']) for c in children[event['id']])
            scope=raw['scope']+'; phase='+json.dumps(paths[event['id']],ensure_ascii=False,separators=(',',':'))
            events.append({**event,'scope':scope,'path':list(paths[event['id']]),'inclusive_seconds':inclusive/1e9,
                           'self_seconds':residue/1e9 if event['children_complete'] else None,
                           'unattributed_seconds':0 if event['children_complete'] else residue/1e9,
                           'evidence':Path(path).name,'evidence_state':'Measured'})
            if clock is not None:events[-1].update(absolute_start_ns=clock['origin_ns']+event['start_ns'],absolute_end_ns=clock['origin_ns']+event['end_ns'],**clock['tracks'][event['track_id']])
            groups[scope].append(events[-1])
        metrics=[];missing=[]
        for scope,group in groups.items():
            correct='passed' if all(e['correctness']=='passed' for e in group) else 'failed' if any(e['correctness']=='failed' for e in group) else 'not_checked'
            extra={'correctness_status':correct,'counter_contract':'sum of recorded calls; inclusive wall durations may overlap; target-declared correctness'}
            for name,value,unit in [('phase.inclusive_time',sum(e['inclusive_seconds'] for e in group),'seconds'),('phase.calls',len(group),'count')]:
                metrics.append(metric(name,value,unit,'target-phase-events',scope,Path(path).name,**extra))
            if all(e['self_seconds'] is not None for e in group):
                metrics.append(metric('phase.self_time',sum(e['self_seconds'] for e in group),'seconds','target-phase-events',scope,Path(path).name,**extra))
            else:
                missing.append(observation('phase.self_time','Missing',reason='Child coverage is not declared complete',scope=scope,unit='seconds',evidence=Path(path).name))
            counters=defaultdict(list)
            for e in group:
                for key,val in e['work'].items():counters[key].append(val)
            for key,values in counters.items():
                require(len(values)==len(group),'Work counter missing from some calls in a stage')
                require(len({(v['unit'],v['evidence_state']) for v in values})==1,'Mixed work units or evidence states')
                metrics.append(metric('phase.work',sum(v['value'] for v in values),values[0]['unit'],'target-phase-events',scope+'; counter='+json.dumps(key),Path(path).name,evidence_state=values[0]['evidence_state'],**extra))
        covered=union_ns((r['start_ns'],r['end_ns']) for r in children[None])
        root=raw['end_ns']-raw['start_ns']
        for name,value in [('phase.covered_wall',covered),('phase.uncovered_wall',root-covered),('phase.root_wall',root)]:
            metrics.append(metric(name,value/1e9,'seconds','target-phase-events',raw['scope'],Path(path).name,
                                  correctness_status='passed' if all(r['correctness']=='passed' for r in rows.values()) and rows else 'not_checked',
                                  counter_contract='root wall-clock union across tracks; not summed thread time'))
        from .activity import analyze
        activity=analyze(events,raw['scope'],clock,Path(path).name)
        # Existing reports retain their metric shape until they opt into activity.
        if any('activity' in e for e in events):
            metrics.extend(activity['metrics'])
        missing.extend(activity['unavailable_observations'])
        return {'clock':clock,'metrics':metrics,'phases':events,'thread_timeline':activity['timeline'],'unavailable_observations':missing,
                'correctness_failed':any(e['correctness']=='failed' for e in events),
                'limitations':['Target-reported monotonic-clock intervals and correctness; independent oracle coverage remains required.',
                    'Self time excludes union of recorded immediate children only when child coverage is declared complete.',
                    'Stage aggregates sum call durations; overlapping calls/tracks are not end-to-end elapsed time.',
                    'Uncovered root wall time is a union complement, not a diagnosis of CPU idle or waiting.',
                    'Inclusive parent/child work counters must not be summed into a pipeline total.']}
    except LabError as error:
        if error.category!='invalid_request':raise
        raise LabError('parse_failed',str(error),{'evidence':str(path)}) from error
    except (OSError,ValueError) as error:
        raise LabError('parse_failed','Cannot read phase report',{'reason':str(error),'evidence':str(path)}) from error
