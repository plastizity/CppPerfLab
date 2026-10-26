"""Bind an archived sample to a reviewed request, then use the existing run API."""
import copy
from pathlib import Path

from .errors import LabError
from .protocol import require
from .samples import verify_sample


def bind_sample(store,identifier,key,template):
    record,sample=verify_sample(store,identifier,key)
    root=store.record_path(identifier,'collection').parent
    request=copy.deepcopy(template)
    parameters=request['workload']['parameters']
    require(not any(k.startswith('case.') for k in parameters),'case.* workload parameters are reserved for replay provenance')
    bindings={'{case_input}':str(root/sample['input']['entry']),'{case_id}':sample['id'],
              '{case_format}':sample['input']['format'],'{case_format_version}':sample['input']['format_version'],
              '{case_expected_or_none}':str(root/sample['expected']['entry']) if 'expected' in sample else '-',
              '{case_expected_format_or_none}':sample['expected']['format'] if 'expected' in sample else '-',
              '{case_expected_version_or_none}':sample['expected']['format_version'] if 'expected' in sample else '-'}
    if 'expected' in sample:bindings['{case_expected}']=str(root/sample['expected']['entry'])
    for name in ('lifecycle','repeats'):
        if name in parameters:bindings['{case_'+name+'}']=str(parameters[name])
    require(any(arg=='{case_input}' for arg in request['target']['argv']),'Target must explicitly consume {case_input}')
    for section in ('target','correctness'):
        vectors=[request[section]['argv']]
        if section=='correctness' and 'sample_validation' in request[section]:vectors.append(request[section]['sample_validation']['argv'])
        for vector in vectors:
            for i,arg in enumerate(vector):
                if '{case_' in arg:
                    require(arg in bindings,'Unknown, unavailable or embedded case binding: '+arg)
                    require('\x00' not in bindings[arg],'Case binding cannot contain NUL in argv')
                    try:bindings[arg].encode('utf-8')
                    except UnicodeError as error:raise LabError('invalid_request','Case binding is not valid UTF-8',{'reason':str(error)}) from error
                    vector[i]=bindings[arg]
    files=[str(root/f['path']) for role in ('input','expected') if role in sample for f in sample[role]['files']]
    request['workload']['inputs']=list(dict.fromkeys([*request['workload']['inputs'],*files]))
    expected=dict(request['workload'].get('expected_hashes',{}))
    expected.update({str(root/f['path']):f['sha256'] for role in ('input','expected') if role in sample for f in sample[role]['files']})
    request['workload']['expected_hashes']=expected
    parameters.update({'case.collection_id':identifier,'case.sample_id':key,'case.source_input_id':sample.get('input_id',sample['id']),'case.input_format':sample['input']['format'],
                       'case.input_version':sample['input']['format_version'],
                       'case.input_files':sample['input']['files'],'case.capture_result':sample['result'],'case.capture_correctness':sample['correctness'],
                       'case.capture_observation':sample['observation'],'case.expected_present':'expected' in sample})
    if 'expected' in sample:
        parameters.update({'case.expected_format':sample['expected']['format'],'case.expected_version':sample['expected']['format_version'],
                           'case.expected_files':sample['expected']['files']})
    request['name']=template['name']+'/'+key
    return request
