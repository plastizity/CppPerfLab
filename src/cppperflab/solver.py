"""Explicit solver-log and independently recomputed per-solve validation boundary."""
import json
import math
from pathlib import Path

from .errors import LabError
from .protocol import fields, positive, require, metric

NORMS = ('l2_absolute', 'l2_relative')
METRIC_IDS=('solver.setup_time','solver.solve_time','solver.total_time','solver.iterations',
            'numerical.residual','numerical.absolute_error','numerical.relative_error')


def validate_contract(contract):
    fields(contract, ('argv','report','residual_norm','max_residual','max_absolute_error','max_relative_error','max_iterations'),
           ('argv','report','residual_norm','max_residual','max_absolute_error','max_relative_error','max_iterations'), 'sample_validation')
    require(isinstance(contract['report'],str) and bool(contract['report']) and
            Path(contract['report']).name==contract['report'] and contract['report'].endswith('.solver.json') and '\x00' not in contract['report'], 'report must be a *.solver.json filename inside the measurement directory')
    require(contract['residual_norm'] in NORMS, 'Unsupported residual norm')
    for key in ('max_residual','max_absolute_error','max_relative_error'):
        positive(contract[key],key,zero=True)
    positive(contract['max_iterations'],'max_iterations',integer=True)


def read_object(path):
    try:
        return json.loads(Path(path).read_text(), parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))
    except (OSError,ValueError) as error:
        raise LabError('parse_failed','Cannot read solver/validation JSON',{'evidence':str(path),'reason':str(error)}) from error


def normalize(report_path, validation_path, contract, problem_id):
    report, verified = read_object(report_path), read_object(validation_path)
    try:
        fields(report,('schema_version','problem_id','termination','iterations','residual_norm','residual_history','setup_seconds','solve_seconds','total_seconds','solution'),
               ('schema_version','problem_id','termination','iterations','residual_norm','residual_history','setup_seconds','solve_seconds','total_seconds','solution'),'solver report')
        fields(verified,('schema_version','problem_id','residual_norm','residual','absolute_error','relative_error'),
               ('schema_version','problem_id','residual_norm','residual','absolute_error','relative_error'),'validator report')
        require(all(type(r['schema_version']) is int and r['schema_version']==1 for r in (report,verified)), 'Unsupported solver report version')
        require(all(r['problem_id']==problem_id and r['residual_norm']==contract['residual_norm'] for r in (report,verified)), 'Solver problem or residual norm mismatch')
        require(report['termination'] in ('converged','max_iterations','breakdown'), 'Unknown termination reason')
        positive(report['iterations'],'iterations',zero=True,integer=True)
        history=report['residual_history']
        require(isinstance(history,list) and len(history)==report['iterations']+1, 'Residual history must include initial and every iteration residual')
        for value in history:positive(value,'residual history',zero=True)
        require(isinstance(report['solution'],list) and bool(report['solution']), 'solution must be a nonempty numeric vector')
        require(all(type(v) in (int,float) and math.isfinite(v) for v in report['solution']), 'Invalid solution vector')
        for key in ('setup_seconds','solve_seconds','total_seconds'):positive(report[key],key,zero=key!='total_seconds')
        parts=report['setup_seconds']+report['solve_seconds']
        require(math.isfinite(parts),'Invalid summed solver duration')
        # Four roundings: two converted component durations, total duration and sum.
        roundoff=sum(math.ulp(v)/2 for v in (report['setup_seconds'],report['solve_seconds'],report['total_seconds'],parts))
        require(parts-report['total_seconds']<=roundoff, 'Setup and solve intervals exceed total; overlapping intervals unsupported')
        for key in ('residual','absolute_error','relative_error'):positive(verified[key],key,zero=True)
    except LabError as error:
        raise LabError('parse_failed',str(error),{'solver_report':str(report_path),'validator_report':str(validation_path)}) from error
    violations=[]
    if report['termination']!='converged':violations.append('termination')
    if report['iterations']>contract['max_iterations']:violations.append('max_iterations')
    for key,limit in (('residual','max_residual'),('absolute_error','max_absolute_error'),('relative_error','max_relative_error')):
        if verified[key]>contract[limit]:violations.append(limit)
    scope='one solve; problem='+problem_id+'; residual_norm='+contract['residual_norm']
    items=[]
    for name,key in [('solver.setup_time','setup_seconds'),('solver.solve_time','solve_seconds'),('solver.total_time','total_seconds'),('solver.iterations','iterations')]:
        items.append(metric(name,report[key],'count' if key=='iterations' else 'seconds',
                            'target-solver-log',scope,Path(report_path).name))
    for name,key in [('numerical.residual','residual'),('numerical.absolute_error','absolute_error'),('numerical.relative_error','relative_error')]:
        items.append(metric(name,verified[key],'ratio' if key=='relative_error' or (key=='residual' and contract['residual_norm']=='l2_relative') else 'problem units',
                            'independent-solver-validation',scope,str(Path('validation')/'stdout.txt')))
    return {'status':'FAIL' if violations else 'PASS','evidence_state':'Measured','violations':violations,'iterations':report['iterations'],
            'termination':report['termination'],'residual_norm':report['residual_norm'],
            'residual_history':history,'verified':verified,'metrics':items,
            'limitations':['Target residual history and clocks are reported instrumentation, not independent hardware measurements.',
                           'Final residual and errors are recomputed by the trusted validator; correctness depends on its coverage.',
                           'Solver total time includes target setup/solve interval; external validator and platform overhead excluded.']}
