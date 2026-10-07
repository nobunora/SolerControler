import base64
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def module():
    path=Path(__file__).resolve().parents[1]/'scripts/backup_complete_remote.py'
    spec=importlib.util.spec_from_file_location('complete_backup',path)
    loaded=importlib.util.module_from_spec(spec);spec.loader.exec_module(loaded)
    return loaded


@pytest.fixture
def backup(module,tmp_path,monkeypatch):
    monkeypatch.setenv('GCP_PROJECT_ID','test-project')
    monkeypatch.setenv('SOLAR_BACKUP_READ_TOKEN','fake-not-a-real-token')
    return module.Backup(tmp_path)


def test_missing_ancestor_subcollection_and_typed_values_are_preserved(backup,monkeypatch):
    root='projects/test-project/databases/(default)/documents'
    child=root+'/parents/missing/children/one'
    document={'name':child,'createTime':'2026-01-01T00:00:00Z','updateTime':'2026-01-02T00:00:00Z',
              'fields':{'large':{'integerValue':'9007199254740993'},'bytes':{'bytesValue':base64.b64encode(b'\0\xff').decode()},
                        'time':{'timestampValue':'2026-01-01T00:00:00.123456789Z'},'empty':{'nullValue':None},
                        'nested':{'mapValue':{'fields':{'ref':{'referenceValue':child}}}}}}
    def items(url,key,**kwargs):
        if key=='databases':return [{'name':'projects/test-project/databases/(default)'}]
        assert kwargs['body']['readTime']
        return {root:['parents'],root+'/parents/missing':['children'],child:[]}[url.removeprefix('https://firestore.googleapis.com/v1/').removesuffix(':listCollectionIds')]
    def pages(url,key,**kwargs):
        assert kwargs['params']['showMissing']=='true'
        if url.endswith('/parents'):yield [{'name':root+'/parents/missing'}]
        else:yield [document]
    monkeypatch.setattr(backup,'items',items);monkeypatch.setattr(backup,'pages',pages)
    monkeypatch.setattr(backup,'optional',lambda *a,**k:{})
    result=backup.firestore()
    assert result['documents']==1 and result['collections']==2
    with gzip.open(backup.output/'firestore/database-0-documents.jsonl.gz','rt',encoding='utf-8') as stream:
        assert json.loads(stream.read())==document
    backup.manifest['stages']['firestore']={'result':result}
    assert backup.verify_local_restore()['firestore_documents_rehydrated']==1


def test_pagination_never_drops_later_page(backup,monkeypatch):
    seen=[]
    def request(url,**kwargs):
        token=kwargs['params'].get('pageToken');seen.append(token)
        return SimpleNamespace(json=lambda:{'items':[2]} if token else {'items':[1],'nextPageToken':'second'})
    monkeypatch.setattr(backup,'request',request)
    assert backup.items('unused','items')==[1,2]
    assert seen==[None,'second']


def test_corrupt_download_never_replaces_existing_good_file(backup,module,monkeypatch,tmp_path):
    path=tmp_path/'blob';path.write_bytes(b'old')
    response=SimpleNamespace(raw=SimpleNamespace(stream=lambda *a,**k:iter([b'corrupt'])),close=lambda:None)
    monkeypatch.setattr(backup,'request',lambda *a,**k:response)
    with pytest.raises(module.ReadFailure,match='SHA256'):
        backup.download('unused',path,digest='0'*64)
    assert path.read_bytes()==b'old'


def test_changed_cached_gcs_object_is_redownloaded(backup,monkeypatch,tmp_path):
    path=tmp_path/'object';path.write_bytes(b'bad')
    response=SimpleNamespace(raw=SimpleNamespace(stream=lambda *a,**k:iter([b'good'])),close=lambda:None)
    monkeypatch.setattr(backup,'request',lambda *a,**k:response)
    backup.download('unused',path,md5=base64.b64encode(hashlib.md5(b'good').digest()).decode())
    assert path.read_bytes()==b'good'


def test_recovery_secrets_are_plaintext_without_machine_keys(backup):
    original={'recovery':'private recovery material'}
    path=backup.write('secrets/value.private.json',original,secret=True)
    assert json.loads(path.read_bytes())==original
    assert b'private recovery material' in path.read_bytes()


def test_partial_stage_is_retried_and_never_reported_complete(backup,monkeypatch):
    calls=[]
    for name in ['local','firestore','firestore_settings','storage','configuration','secrets','logs','registry']:
        monkeypatch.setattr(backup,name,lambda:{})
    def drive():
        calls.append('drive')
        backup.manifest['gaps'].append({'category':'drive_archive','reason':'test authorization gap'})
        return {'copied':False}
    monkeypatch.setattr(backup,'drive',drive)
    assert backup.run()==2
    assert backup.run()==2
    assert calls==['drive','drive']
    assert backup.manifest['stages']['drive']['status']=='partial'
    assert len(backup.manifest['gaps'])==1


def test_secret_manager_bytes_can_be_restored_without_decryption(backup,monkeypatch):
    raw=b'plain recovery value\n'
    monkeypatch.setattr(backup,'items',lambda url,key,**kwargs:
                        [{'name':'projects/test-project/secrets/one'}] if key=='secrets'
                        else [{'name':'projects/test-project/secrets/one/versions/1','state':'ENABLED'}])
    monkeypatch.setattr(backup,'request',lambda *a,**k:SimpleNamespace(json=lambda:
                        {'payload':{'data':base64.b64encode(raw).decode()}}))
    monkeypatch.setattr(backup,'optional',lambda *a,**k:{})
    result=backup.secrets()
    inventory=json.loads((backup.output/'secrets/secret_versions.private.json').read_bytes())
    path=backup.output/inventory[0]['versions'][0]['recovery_file']
    assert path.read_bytes()==raw and result['encrypted'] is False
    assert backup.verify_local_restore()['plaintext_secret_read_verified'] is True


def test_resume_rejects_changed_successful_checkpoint(backup,module):
    path=backup.output/'copied-file';path.write_bytes(b'good')
    backup.manifest['stages']['local']={'status':'success','files':{'copied-file':module.sha256(path)}}
    path.write_bytes(b'corrupted')
    with pytest.raises(module.ReadFailure,match='checkpoint checksum mismatch'):
        backup.run()


def test_cached_image_export_requires_explicit_authorization(backup,module,monkeypatch):
    monkeypatch.setattr(backup,'request',lambda *a,**k:pytest.fail('Export must not make a request'))
    with pytest.raises(module.ReadFailure,match='not authorized'):
        backup.recover_cloud_run_image('unused',[])
    assert backup.manifest['remote_writes']==0


def test_seed_reuses_only_verified_immutable_bytes_and_retains_original(backup,module,tmp_path,monkeypatch):
    seed=tmp_path/'seed';seed.mkdir()
    relative='registry/oci/blobs/sha256/'+'a'*64
    original=seed/relative;original.parent.mkdir(parents=True);original.write_bytes(b'original')
    (seed/'manifest.private.json').write_text(json.dumps({'project':'test-project','files':{
        relative:{'sha256':module.sha256(original)},'configuration/cloud_run_jobs.json':{'sha256':'unused'}}}))
    backup.seed_immutable_files(seed)
    assert (backup.output/relative).read_bytes()==b'original'
    assert not (backup.output/'configuration/cloud_run_jobs.json').exists()
    response=SimpleNamespace(raw=SimpleNamespace(stream=lambda *a,**k:iter([b'new'])),close=lambda:None)
    monkeypatch.setattr(backup,'request',lambda *a,**k:response)
    backup.download('unused',backup.output/relative,digest=hashlib.sha256(b'new').hexdigest())
    assert original.read_bytes()==b'original'
    assert (backup.output/relative).read_bytes()==b'new'


@pytest.mark.parametrize('short_execution_name',[False,True])
def test_cached_image_export_records_replacement_and_resumes_without_reposting(backup,monkeypatch,short_execution_name):
    backup.recover_required_images=True
    uri='testregion-docker.pkg.dev/test-project/testrepo/runner@sha256:original'
    restored='testregion-docker.pkg.dev/test-project/testrepo/recovered@sha256:recovered'
    job='projects/test-project/locations/testregion/jobs/one'
    execution=job+'/executions/last'
    backup.write('configuration/cloud_run_jobs.json',{'jobs':[{'name':job,'template':{'template':{'containers':[{'image':uri}]}}}]})
    calls=[]
    def request(url,**kwargs):
        calls.append((url,kwargs))
        if url.endswith(':exportImage'):
            assert kwargs['retry'] is False
            assert kwargs['body']=={'destinationRepo':'testregion-docker.pkg.dev/test-project/testrepo'}
            result={'operationId':'export-one'}
        elif url.endswith(':exportStatus'):
            result={'operationState':'FINISHED','imageExportStatuses':[{'exportedImageDigest':'sha256:recovered','status':{'code':0}}]}
        elif url.endswith('/executions/last'):result={'template':{'containers':[{'image':uri}]}}
        else:result={'latestCreatedExecution':{'name':'last' if short_execution_name else execution}}
        return SimpleNamespace(json=lambda:result)
    monkeypatch.setattr(backup,'request',request)
    monkeypatch.setattr(backup,'items',lambda *a,**k:[{'uri':restored}])
    repos=[{'name':'projects/test-project/locations/testregion/repositories/testrepo'}]
    assert backup.recover_cloud_run_image(uri,repos)==restored
    assert backup.recover_cloud_run_image(uri,repos)==restored
    assert len([url for url,_ in calls if url.endswith(':exportImage')])==1
    assert backup.manifest['remote_writes']==1


def test_cached_image_export_refuses_cross_project_destination(backup,module,monkeypatch):
    backup.recover_required_images=True
    monkeypatch.setattr(backup,'request',lambda *a,**k:pytest.fail('Cross-project export must not make a request'))
    with pytest.raises(module.ReadFailure,match='project mismatch'):
        backup.recover_cloud_run_image('testregion-docker.pkg.dev/other-project/testrepo/runner@sha256:original',[])


def test_offline_restore_detects_duplicate_document_path(backup,module):
    directory=backup.output/'firestore';directory.mkdir()
    row={'name':'same','fields':{'a':{'integerValue':'1'}}}
    with gzip.open(directory/'database-0-documents.jsonl.gz','wt',encoding='utf-8') as stream:
        stream.write(json.dumps(row)+'\n'+json.dumps(row)+'\n')
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):backup.verify_local_restore()
