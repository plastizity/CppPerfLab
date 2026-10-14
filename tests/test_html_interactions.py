"""Execute the offline UI event handlers with a small DOM fixture, no browser claims."""
import json
import shutil
import subprocess
import unittest

from cppperflab.html_report import JS


class HtmlInteractionTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is needed to execute UI handlers')
    def test_filter_scale_switch_and_download_use_presented_data(self):
        fixture = r'''
const assert=require('node:assert/strict');
const rows=[{textContent:'Kernel A',hidden:false},{textContent:'Kernel B',hidden:false}];
const input={value:'',addEventListener(_name,fn){this.handler=fn;},
 closest(){return {querySelectorAll(){return rows;}};}};
const plots=[{dataset:{scale:'linear'},hidden:false},{dataset:{scale:'log'},hidden:true}];
const select={value:'linear',addEventListener(_name,fn){this.handler=fn;},
 closest(){return {querySelectorAll(){return plots;}};}};
const button={addEventListener(_name,fn){this.handler=fn;}};
const snapshot={textContent:JSON.stringify({schema_version:1,message:'<safe>中文'})};
let clicked=false,createdBlob=null,revoked=null;
const anchor={click(){clicked=true;}};
globalThis.document={querySelectorAll(query){
 if(query==='.table-search')return [input];
 if(query==='.scale-switch select')return [select];
 throw Error(query);
},getElementById(id){return id==='download-view'?button:snapshot;},createElement(){return anchor;}};
globalThis.URL={createObjectURL(blob){createdBlob=blob;return 'blob:test';},revokeObjectURL(url){revoked=url;}};
'''
        checks = r'''
input.value='kernel b';input.handler();assert.equal(rows[0].hidden,true);assert.equal(rows[1].hidden,false);
input.value='';input.handler();assert.equal(rows[0].hidden,false);
select.value='log';select.handler();assert.equal(plots[0].hidden,true);assert.equal(plots[1].hidden,false);
button.handler();assert.equal(clicked,true);assert.equal(anchor.download,'cppperflab-analysis.json');
assert.equal(createdBlob.type,'application/json');assert.equal(revoked,'blob:test');
createdBlob.text().then(text=>{assert.deepEqual(JSON.parse(text),JSON.parse(snapshot.textContent));});
'''
        result = subprocess.run([shutil.which('node'), '-e', fixture+JS+checks],
                                capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
