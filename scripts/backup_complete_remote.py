"""Disaster-recovery capture; invoke through the production env wrapper.

Firestore values are stored in the lossless REST wire format, including every
subcollection and missing ancestor. This must never become the dashboard's
flattened SQLite sync or a recent-days export. Recovery secrets are plaintext
by explicit operator request; the wrapper restricts the ignored output ACL.
Capture is read-only unless required cached-image recovery is explicitly enabled.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import os
import sqlite3
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

import requests


class ReadFailure(RuntimeError):
    """An error that is safe to print without resource names or credentials."""
    api_reason: str | None = None


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


class Backup:
    def __init__(self, output: Path, *, recover_required_images: bool=False) -> None:
        self.recover_required_images=recover_required_images
        self.output = output.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.project = os.environ['GCP_PROJECT_ID']
        self.session = requests.Session()
        self.session.headers['Authorization'] = 'Bearer ' + os.environ['SOLAR_BACKUP_READ_TOKEN']
        self.manifest_path = self.output / 'manifest.private.json'
        if self.manifest_path.exists():
            self.manifest = json.loads(self.manifest_path.read_text(encoding='utf-8'))
            if self.manifest['project'] != self.project:
                raise ReadFailure('Resume project mismatch')
        else:
            self.manifest = {'schema_version':1, 'project':self.project, 'started_at':datetime.now(timezone.utc).isoformat(),
                             'remote_writes':0, 'stages':{}, 'gaps':[], 'files':{}}
        self.save()

    def save(self) -> None:
        temp = self.manifest_path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(self.manifest_path)

    def write(self, name: str, data: Any, *, secret: bool=False) -> Path:
        path = self.output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(data, ensure_ascii=False).encode('utf-8')
        # Do not encrypt recovery material: the operator must be able to recover
        # from another machine without depending on this Windows user's keys.
        path.write_bytes(content)
        return path

    def request(self, url: str, *, method: str='GET', params: dict[str, Any] | None=None, body: dict[str, Any] | None=None, headers: dict[str, str] | None=None, stream: bool=False, retry: bool=True) -> requests.Response:
        attempts=4 if retry else 1
        for attempt in range(attempts):
            try:
                response = self.session.request(method, url, params=params, json=body, headers=headers, stream=stream, timeout=(30,120))
            except requests.RequestException:
                if attempt == attempts-1: raise ReadFailure('Network read failed') from None
                time.sleep(2**attempt)
                continue
            if response.status_code in [429,500,502,503,504] and attempt < attempts-1:
                response.close(); time.sleep(2**attempt); continue
            if not response.ok:
                try: details=response.json()
                except ValueError: details={}
                self.write('diagnostics/http-'+hashlib.sha256(url.encode()).hexdigest()+'.json',{'url':url,'status':response.status_code,'error':details})
                failure=ReadFailure(f'HTTP {response.status_code} read failed')
                reasons=[d.get('reason') for d in details.get('error',{}).get('details',[]) if isinstance(d,dict)]
                failure.api_reason='SERVICE_DISABLED' if 'SERVICE_DISABLED' in reasons else None
                raise failure
            return response
        raise ReadFailure('Network read failed')

    def pages(self, url: str, key: str, *, params: dict[str, Any] | None=None, method: str='GET', body: dict[str, Any] | None=None) -> Iterator[list[Any]]:
        token = None
        while True:
            query = dict(params or {})
            payload = dict(body or {})
            if token:
                (payload if method=='POST' else query)['pageToken'] = token
            data = self.request(url, method=method, params=query, body=payload if method=='POST' else None).json()
            yield data.get(key, [])
            token = data.get('nextPageToken')
            if not token: break

    def items(self, *args: Any, **kwargs: Any) -> list[Any]:
        return [item for page in self.pages(*args, **kwargs) for item in page]

    def optional(self, category: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            return cast(dict[str, Any], self.request(url, **kwargs).json())
        except ReadFailure as exc:
            if exc.api_reason=='SERVICE_DISABLED':
                return {'not_applicable':'API was not enabled at capture time'}
            self.manifest['gaps'].append({'category':category,'reason':str(exc)})
            self.save()
            return {'unavailable':str(exc)}

    def download(self, url: str, path: Path, *, params: dict[str, Any] | None=None, headers: dict[str, str] | None=None, digest: str | None=None, md5: str | None=None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if digest and sha256(path)==digest:return
            if md5 and base64.b64encode(hashlib.md5(path.read_bytes()).digest()).decode()==md5:return
        temporary = path.with_suffix('.partial')
        response = self.request(url, params=params, headers=headers, stream=True)
        checksum = hashlib.md5()  # GCS's transport checksum, not a security digest.
        with temporary.open('wb') as stream:
            for chunk in response.raw.stream(1024*1024, decode_content=False):
                stream.write(chunk); checksum.update(chunk)
        response.close()
        if digest and sha256(temporary) != digest:
            raise ReadFailure('Downloaded SHA256 mismatch')
        if md5 and base64.b64encode(checksum.digest()).decode() != md5:
            raise ReadFailure('Downloaded object MD5 mismatch')
        temporary.replace(path)

    def firestore(self) -> dict[str, Any]:
        base = 'https://firestore.googleapis.com/v1/'
        databases = self.items(base+f'projects/{self.project}/databases','databases')
        self.write('firestore/databases.json',databases)
        total, collections = 0, []
        read_time = datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')
        for index, database in enumerate(databases):
            name = database['name']; root = name+'/documents'
            root_ids=self.items(base+root+':listCollectionIds','collectionIds',method='POST',body={'pageSize':1000,'readTime':read_time})
            queue = [(root,root_ids)]
            path = self.output/f'firestore/database-{index}-documents.jsonl.gz'
            path.parent.mkdir(parents=True,exist_ok=True)
            with gzip.open(path,'wt',encoding='utf-8') as stream, ThreadPoolExecutor(max_workers=12) as executor:
                while queue:
                    parent, ids = queue.pop()
                    for collection in ids:
                        count = 0
                        for page in self.pages(base+parent+'/'+quote(collection,safe=''),'documents',params={'pageSize':1000,'showMissing':'true','readTime':read_time}):
                            for document in page:
                                if 'updateTime' in document:
                                    stream.write(json.dumps(document,ensure_ascii=False)+'\n'); total+=1;count+=1
                            def child_collections(document: dict[str, Any]) -> tuple[str, list[Any]]:
                                child=document['name']
                                ids=self.items(base+child+':listCollectionIds','collectionIds',method='POST',body={'pageSize':1000,'readTime':read_time})
                                return child,ids
                            queue.extend((child,ids) for child,ids in executor.map(child_collections,page) if ids)
                        collections.append({'path':parent+'/'+collection,'documents':count})
                        if parent==root: print('Firestore root collection copied; documents',count,flush=True)
        self.write('firestore/collections.json',collections)
        return {'databases':len(databases),'documents':total,'collections':len(collections),'read_time':read_time,'wire_format':'Firestore REST typed values'}

    def firestore_settings(self) -> dict[str, Any]:
        databases=json.loads((self.output/'firestore/databases.json').read_text(encoding='utf-8'))
        count=0
        for index,database in enumerate(databases):
            base='https://firestore.googleapis.com/v1/'+database['name']+'/collectionGroups/-/'
            # This backend supports only the API default pageSize (0).
            indexes=self.items(base+'indexes','indexes')
            fields={row['name']:row for filter_value in ['indexConfig.usesAncestorConfig:false','ttlConfig:*'] for row in self.items(base+'fields','fields',params={'filter':filter_value})}
            self.write(f'firestore/database-{index}-indexes.json',{'indexes':indexes})
            self.write(f'firestore/database-{index}-fields.json',{'fields':list(fields.values())})
            count+=len(indexes)
        self.manifest['gaps']=[g for g in self.manifest['gaps'] if g['category'] not in ['firestore_indexes','firestore_field_settings']]
        return {'indexes':count,'databases':len(databases)}

    def storage(self) -> dict[str, Any]:
        buckets = self.items(f'https://storage.googleapis.com/storage/v1/b?project={self.project}','items')
        self.write('storage/buckets.json',buckets)
        count, size, soft_count = 0,0,0
        for i,bucket in enumerate(buckets):
            name=bucket['name'];base=f'https://storage.googleapis.com/storage/v1/b/{quote(name,safe="")}'
            self.write(f'storage/bucket-{i}-iam.json',self.optional('storage_iam',base+'/iam'))
            objects=self.items(base+'/o','items',params={'versions':'true','maxResults':1000})
            soft=self.items(base+'/o','items',params={'softDeleted':'true','maxResults':1000})
            self.write(f'storage/bucket-{i}-objects.json',objects)
            self.write(f'storage/bucket-{i}-soft-deleted.json',soft)
            soft_count+=len(soft)
            available_by_md5={}
            for item in objects:
                identity=hashlib.sha256((name+'/'+item['name']+'/'+item['generation']).encode()).hexdigest()
                target=self.output/f'storage/objects/{identity}'
                self.download(base+'/o/'+quote(item['name'],safe=''),target,params={'alt':'media','generation':item['generation']},headers={'Accept-Encoding':'gzip'},md5=item.get('md5Hash'))
                item['local_file']=str(target.relative_to(self.output));item['local_sha256']=sha256(target)
                count+=1;size+=target.stat().st_size
                if item.get('md5Hash'): available_by_md5[(item['md5Hash'],item.get('size'))]=item['local_file']
            for item in soft:
                content=available_by_md5.get((item.get('md5Hash'),item.get('size')))
                if content:
                    item['local_file']=content;item['content_status']='identical_to_readable_generation'
                else:
                    item['content_status']='requires_remote_restore_before_download'
                    self.manifest['gaps'].append({'category':'soft_deleted_object','reason':'Provider does not permit reading soft-deleted content without a remote restore','bucket_index':i,'object_hash':hashlib.sha256(item['name'].encode()).hexdigest()})
            self.write(f'storage/bucket-{i}-objects.json',objects)
            self.write(f'storage/bucket-{i}-soft-deleted.json',soft)
            print('Storage bucket copied; objects',len(objects),'soft-deleted',len(soft),flush=True)
        return {'buckets':len(buckets),'readable_generations':count,'bytes':size,'soft_deleted_generations':soft_count}

    def recover_cloud_run_image(self, uri: str, repos: list[dict[str, Any]]) -> str:
        """Copy a required cached image into an existing repo, only with opt-in.

        This creates a recovery artifact; it never executes or updates a job.
        The original and exported digests can differ and must be mapped explicitly.
        """
        if not self.recover_required_images:
            raise ReadFailure('Required image recovery was not authorized')
        host,package_digest=uri.split('/',1)
        package,digest=package_digest.rsplit('@',1)
        project,repository=package.split('/')[:2]
        if project!=self.project:
            raise ReadFailure('Required image project mismatch')
        repo=next((r for r in repos if r['name'].endswith('/repositories/'+repository)
                   and host.startswith(r['name'].split('/locations/')[1].split('/')[0]+'-docker.pkg.dev')),None)
        if not repo:raise ReadFailure('Required image destination repository mismatch')
        jobs=json.loads((self.output/'configuration/cloud_run_jobs.json').read_bytes()).get('jobs',[])
        job=next((j for j in jobs if any(c.get('image')==uri for c in j['template']['template']['containers'])),None)
        if not job:raise ReadFailure('Required image has no matching job')
        fresh=self.request('https://run.googleapis.com/v2/'+job['name']).json()
        execution=fresh.get('latestCreatedExecution',{}).get('name')
        if not execution:raise ReadFailure('Required image has no retained execution')
        if '/' not in execution:execution=job['name']+'/executions/'+execution
        if not execution.startswith(job['name']+'/executions/'):
            raise ReadFailure('Required image execution does not belong to the job')
        execution_data=self.request('https://run.googleapis.com/v2/'+execution).json()
        if not any(c.get('image','').endswith('@'+digest) for c in execution_data.get('template',{}).get('containers',[])):
            raise ReadFailure('Execution image does not match required image')
        path='registry/cloud_run_exports/'+hashlib.sha256(uri.encode()).hexdigest()+'.json'
        metadata=self.request('https://run.googleapis.com/v2/'+execution+':exportImageMetadata').json()
        self.write(path.removesuffix('.json')+'-metadata.json',metadata)
        checkpoint=self.output/path
        export=json.loads(checkpoint.read_bytes()) if checkpoint.exists() else {}
        if not export.get('operationId'):
            # Record an inconclusive attempt before the non-idempotent request.
            if export.get('request_started'):
                raise ReadFailure('Previous export request is inconclusive; inspect before retrying')
            export={'source':uri,'execution':execution,'request_started':True}
            self.write(path,export)
            self.manifest['remote_write_attempts']=self.manifest.get('remote_write_attempts',0)+1;self.save()
            reply=self.request('https://run.googleapis.com/v2/'+execution+':exportImage',method='POST',
                               body={'destinationRepo':host+'/'+project+'/'+repository},retry=False).json()
            self.manifest['remote_writes']=self.manifest.get('remote_writes',0)+1;self.save()
            export.update(reply);self.write(path,export)
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            status=self.request('https://run.googleapis.com/v2/'+export['execution']+'/'+export['operationId']+':exportStatus').json()
            export['status']=status;self.write(path,export)
            if status.get('operationState')=='FINISHED':
                results=status.get('imageExportStatuses',[])
                if not results or any(v.get('status',{}).get('code',0)!=0 for v in results):
                    raise ReadFailure('Cached image export failed; private status retained')
                exported={v['exportedImageDigest'] for v in results}
                rows=self.items('https://artifactregistry.googleapis.com/v1/'+repo['name']+'/dockerImages','dockerImages',params={'pageSize':1000})
                matches=[r['uri'] for r in rows if any(r['uri']==d or r['uri'].endswith('@'+d) for d in exported)]
                if len(matches)!=1:raise ReadFailure('Exported image could not be resolved uniquely')
                export['recovery_reference']=matches[0];self.write(path,export)
                return cast(str, matches[0])
            time.sleep(5)
        raise ReadFailure('Cached image export still running; resume with the same backup')

    def registry(self) -> dict[str, Any]:
        # gcloud's repository list omits registryUri; derive it from the resource.
        result=subprocess.run(['pwsh','-NoProfile','-File','scripts/gcloud.ps1','artifacts','repositories','list','--project',self.project,'--format=json'],capture_output=True,text=True,encoding='utf-8')
        if result.returncode: raise ReadFailure('Repository inventory failed')
        repos=json.loads(result.stdout);self.write('registry/repositories.json',repos)
        images=[];blobs=set()
        auth='Basic '+base64.b64encode(('oauth2accesstoken:'+os.environ['SOLAR_BACKUP_READ_TOKEN']).encode()).decode()
        accept=', '.join(['application/vnd.oci.image.index.v1+json','application/vnd.docker.distribution.manifest.list.v2+json','application/vnd.oci.image.manifest.v1+json','application/vnd.docker.distribution.manifest.v2+json'])
        def capture(host: str, package: str, digest: str) -> dict[str, Any]:
            target=self.output/'registry/oci/blobs/sha256'/digest.split(':',1)[1]
            url=f'https://{host}/v2/{package}/manifests/{digest}'
            self.download(url,target,headers={'Authorization':auth,'Accept':accept},digest=digest.split(':',1)[1])
            manifest=json.loads(target.read_bytes());blobs.add(digest)
            for child in manifest.get('manifests',[]):capture(host,package,child['digest'])
            for item in [manifest.get('config')]+manifest.get('layers',[]):
                if not item:continue
                value=item['digest'];blob=self.output/'registry/oci/blobs/sha256'/value.split(':',1)[1]
                self.download(f'https://{host}/v2/{package}/blobs/{value}',blob,headers={'Authorization':auth,'Accept-Encoding':'identity'},digest=value.split(':',1)[1])
                blobs.add(value)
            return {'mediaType':manifest.get('mediaType','application/vnd.docker.distribution.manifest.v2+json'),'digest':digest,'size':target.stat().st_size}
        descriptors=[]
        for repo in repos:
            if repo.get('format')!='DOCKER':
                self.manifest['gaps'].append({'category':'non_docker_repository','reason':'Format not covered'});continue
            rows=self.items('https://artifactregistry.googleapis.com/v1/'+repo['name']+'/dockerImages','dockerImages',params={'pageSize':1000})
            for image in rows:
                uri=image['uri']; host,package_digest=uri.split('/',1);package,digest=package_digest.rsplit('@',1)
                descriptor=capture(host,package,digest)
                descriptor['annotations']={'org.opencontainers.image.ref.name':uri}
                descriptors.append(descriptor);images.append(image)
                print('Container copied and digest verified;',len(images),'images',flush=True)
        deployed_refs=set()
        def collect_refs(value: Any) -> None:
            if isinstance(value,dict):
                for key,item in value.items():
                    if key=='image' and isinstance(item,str):deployed_refs.add(item)
                    else:collect_refs(item)
            elif isinstance(value,list):
                for item in value:collect_refs(item)
        for category in ['cloud_run_jobs','cloud_run_services']:
            path=self.output/'configuration'/f'{category}.json'
            if path.exists():collect_refs(json.loads(path.read_bytes()))
        extra=0
        for uri in sorted(deployed_refs):
            if '@sha256:' not in uri:continue
            host,package_digest=uri.split('/',1);package,digest=package_digest.rsplit('@',1)
            if (self.output/'registry/oci/blobs/sha256'/digest.split(':',1)[1]).exists():continue
            try:
                descriptor=capture(host,package,digest)
            except ReadFailure as exc:
                try:
                    if not self.recover_required_images:raise exc
                    recovery_uri=self.recover_cloud_run_image(uri,repos)
                    recovery_host,recovery_package_digest=recovery_uri.split('/',1)
                    recovery_package,recovery_digest=recovery_package_digest.rsplit('@',1)
                    descriptor=capture(recovery_host,recovery_package,recovery_digest)
                    descriptor['annotations']={'solarcontroller.recovery.source':uri,'solarcontroller.recovery.reference':recovery_uri}
                    print('Required cached image copied and digest verified',flush=True)
                except ReadFailure as recovery_error:
                    self.manifest['gaps'].append({'category':'deployed_image_unavailable','reason':str(recovery_error),
                                                  'reference_hash':hashlib.sha256(uri.encode()).hexdigest()})
                    continue
            descriptor.setdefault('annotations',{})['org.opencontainers.image.ref.name']=uri
            descriptors.append(descriptor);extra+=1
        self.write('registry/images.json',images)
        self.write('registry/oci/oci-layout',{'imageLayoutVersion':'1.0.0'})
        self.write('registry/oci/index.json',{'schemaVersion':2,'manifests':descriptors})
        return {'repositories':len(repos),'images':len(images),'additional_deployed_digests':extra,'unique_blobs':len(blobs),'stored_bytes':sum(p.stat().st_size for p in (self.output/'registry/oci/blobs/sha256').glob('*'))}

    def configuration(self) -> dict[str, Any]:
        project=self.project;prefix=f'projects/{project}'
        specs={
            'project':f'https://cloudresourcemanager.googleapis.com/v1/{prefix}',
            'enabled_services':f'https://serviceusage.googleapis.com/v1/{prefix}/services?filter=state:ENABLED&pageSize=200',
            'service_accounts':f'https://iam.googleapis.com/v1/{prefix}/serviceAccounts?pageSize=100',
            'cloud_run_services':f'https://run.googleapis.com/v2/{prefix}/locations/-/services?pageSize=100',
            'cloud_run_jobs':f'https://run.googleapis.com/v2/{prefix}/locations/{os.environ["GCP_REGION"]}/jobs',
            'logging_buckets':f'https://logging.googleapis.com/v2/{prefix}/locations/-/buckets',
            'logging_sinks':f'https://logging.googleapis.com/v2/{prefix}/sinks?pageSize=1000',
            'logging_exclusions':f'https://logging.googleapis.com/v2/{prefix}/exclusions?pageSize=1000',
            'firestore_rules':f'https://firebaserules.googleapis.com/v1/{prefix}/rulesets?pageSize=1000',
            'firebase_releases':f'https://firebaserules.googleapis.com/v1/{prefix}/releases?pageSize=1000',
            'cloud_assets':f'https://cloudasset.googleapis.com/v1/{prefix}:searchAllResources?pageSize=1000',
        }
        config={key:self.optional(key,url) for key,url in specs.items()}
        config['project_iam']=self.optional('project_iam',f'https://cloudresourcemanager.googleapis.com/v1/{prefix}:getIamPolicy',method='POST',body={})
        location=os.environ['GCP_SCHEDULER_REGION']
        config['scheduler_jobs']=self.optional('scheduler_jobs',f'https://cloudscheduler.googleapis.com/v1/{prefix}/locations/{location}/jobs')
        for key,data in config.items():
            token=data.get('nextPageToken')
            while token:
                url=specs.get(key)
                if not url:
                    self.manifest['gaps'].append({'category':key,'reason':'Additional inventory page required'});break
                page=self.request(url,params={'pageToken':token}).json()
                for item_key,items in page.items():
                    if isinstance(items,list):data.setdefault(item_key,[]).extend(items)
                token=page.get('nextPageToken')
            data.pop('nextPageToken',None)
            self.write(f'configuration/{key}.json',data,secret=True)
        for i,ruleset in enumerate(config['firestore_rules'].get('rulesets',[])):
            detail=self.request('https://firebaserules.googleapis.com/v1/'+ruleset['name']).json()
            self.write(f'configuration/ruleset-{i}.json',detail,secret=True)
        for collection,key in [('cloud_run_services','services'),('cloud_run_jobs','jobs')]:
            for i,resource in enumerate(config[collection].get(key,[])):
                iam=self.optional('run_iam','https://run.googleapis.com/v2/'+resource['name']+':getIamPolicy')
                self.write(f'configuration/{collection}-{i}-iam.json',iam,secret=True)
        return {'inventory_categories':len(config),'encrypted':False}

    def secrets(self) -> dict[str, Any]:
        base='https://secretmanager.googleapis.com/v1/'
        secrets=self.items(base+f'projects/{self.project}/secrets','secrets',params={'pageSize':1000})
        output=[];values=0
        for secret in secrets:
            versions=self.items(base+secret['name']+'/versions','versions',params={'pageSize':1000})
            for version in versions:
                if version['state']=='ENABLED':
                    version['recovery_payload']=self.request(base+version['name']+':access').json()['payload'];values+=1
                    payload=base64.b64decode(version['recovery_payload']['data'])
                    value_path='secrets/values/'+hashlib.sha256(version['name'].encode()).hexdigest()+'.value'
                    local=self.output/value_path;local.parent.mkdir(parents=True,exist_ok=True);local.write_bytes(payload)
                    version['recovery_file']=value_path
                elif version['state']=='DISABLED':
                    self.manifest['gaps'].append({'category':'disabled_secret_version','reason':'Payload requires enabling the version to read it'})
            output.append({'secret':secret,'versions':versions,'iam':self.optional('secret_iam',base+secret['name']+':getIamPolicy')})
        self.write('secrets/secret_versions.private.json',output,secret=True)
        return {'secrets':len(secrets),'enabled_version_payloads':values,'encrypted':False}

    def logs(self) -> dict[str, Any]:
        path=self.output/'logs/all_retained_entries.jsonl.gz';path.parent.mkdir(parents=True,exist_ok=True)
        count=0
        with gzip.open(path,'wt',encoding='utf-8') as stream:
            for page in self.pages('https://logging.googleapis.com/v2/entries:list','entries',method='POST',body={'resourceNames':[f'projects/{self.project}'],'pageSize':1000,'orderBy':'timestamp asc'}):
                for row in page:stream.write(json.dumps(row,ensure_ascii=False)+'\n');count+=1
        return {'entries':count,'period':'all entries still retained by provider'}

    def local(self) -> dict[str, Any]:
        directory=self.output/'local';directory.mkdir(exist_ok=True)
        for name in ['.env']:
            (directory/name).write_bytes(Path(name).read_bytes())
        subprocess.run(['git','bundle','create',str(directory/'source.bundle'),'--all'],check=True,capture_output=True)
        subprocess.run(['git','bundle','verify',str(directory/'source.bundle')],check=True,capture_output=True)
        for mode,flag in [('unstaged',''),('staged','--cached')]:
            command=['git','diff','--binary']+([flag] if flag else [])
            (directory/(mode+'.patch')).write_bytes(subprocess.run(command,check=True,capture_output=True).stdout)
        for filename in ['scripts/backup_complete_remote.py','docs/current/ops/COMPLETE_LOCAL_RECOVERY_BACKUP_JA.md','tests/test_complete_remote_backup.py']:
            path=directory/'working_source'/filename;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(Path(filename).read_bytes())
        source=sqlite3.connect('file:artifacts/solar_monitor.db?mode=ro',uri=True)
        destination=sqlite3.connect(directory/'solar_monitor.db');source.backup(destination)
        destination.close();source.close()
        revision=subprocess.run(['git','rev-parse','HEAD'],check=True,capture_output=True,text=True).stdout.strip()
        return {'source_revision':revision,'git_bundle_verified':True,'env_encrypted':False,'sqlite_backup':True}

    def drive(self) -> dict[str, Any]:
        folder=os.environ.get('DRIVE_BACKUP_FOLDER_ID')
        if not folder:return {'configured':False}
        params={'q':f"'{folder}' in parents and trashed=false",'fields':'nextPageToken,files(id,name,mimeType,size,md5Checksum,modifiedTime)','pageSize':1000,'supportsAllDrives':'true','includeItemsFromAllDrives':'true'}
        original=self.session.headers['Authorization']
        try:
            rows=self.items('https://www.googleapis.com/drive/v3/files','files',params=params)
        except ReadFailure:
            account=os.environ.get('GCP_RUN_SERVICE_ACCOUNT')
            if account:
                result=subprocess.run(['pwsh','-NoProfile','-File','scripts/gcloud.ps1','auth','print-access-token','--impersonate-service-account',account,'--scopes=https://www.googleapis.com/auth/drive.readonly'],capture_output=True,text=True,encoding='utf-8')
                if result.returncode==0:
                    self.session.headers['Authorization']='Bearer '+result.stdout.strip()
                    try:rows=self.items('https://www.googleapis.com/drive/v3/files','files',params=params)
                    except ReadFailure:rows=None
                    if rows is not None:
                        self.manifest['gaps']=[g for g in self.manifest['gaps'] if g['category']!='drive_archive']
                        for item in rows:
                            if item['mimeType'].startswith('application/vnd.google-apps.'):
                                self.manifest['gaps'].append({'category':'drive_native_file','reason':'Native export required'});continue
                            target=self.output/'drive/files'/item['id']
                            self.download('https://www.googleapis.com/drive/v3/files/'+item['id'],target,params={'alt':'media','supportsAllDrives':'true'},headers={'Accept-Encoding':'identity'},md5=item.get('md5Checksum'))
                            item['local_file']=str(target.relative_to(self.output))
                        self.write('drive/files.json',rows)
                        self.session.headers['Authorization']=original
                        return {'configured':True,'files':len(rows),'auth_method':'configured_service_account_impersonation'}
            # Application Default Credentials may have Drive scope separately from gcloud.
            import google.auth
            import google.auth.transport.requests
            try:
                credentials: Any
                credentials,_=google.auth.default(scopes=['https://www.googleapis.com/auth/drive.readonly'])
                credentials.refresh(google.auth.transport.requests.Request())
                if not credentials.token:
                    raise ReadFailure('Drive credentials did not provide an access token')
                self.session.headers['Authorization']='Bearer '+credentials.token
                rows=self.items('https://www.googleapis.com/drive/v3/files','files',params=params)
            except Exception:
                self.manifest['gaps'].append({'category':'drive_archive','reason':'Drive read authorization unavailable'})
                self.session.headers['Authorization']=original
                return {'configured':True,'copied':False}
        if rows is None:
            raise ReadFailure('Drive file inventory was not obtained')
        for item in rows:
            if item['mimeType'].startswith('application/vnd.google-apps.'):
                self.manifest['gaps'].append({'category':'drive_native_file','reason':'Native export required'});continue
            target=self.output/'drive/files'/item['id']
            self.download('https://www.googleapis.com/drive/v3/files/'+item['id'],target,params={'alt':'media','supportsAllDrives':'true'},headers={'Accept-Encoding':'identity'},md5=item.get('md5Checksum'))
            item['local_file']=str(target.relative_to(self.output))
        self.write('drive/files.json',rows)
        self.session.headers['Authorization']=original
        return {'configured':True,'files':len(rows)}

    def verify_local_restore(self) -> dict[str, Any]:
        """Rehydrate wire documents offline, checking every field and path."""
        database=sqlite3.connect(':memory:')
        database.execute('create table restored_documents (name text primary key, wire_json text not null)')
        count=0
        for path in (self.output/'firestore').glob('database-*-documents.jsonl.gz'):
            with gzip.open(path,'rt',encoding='utf-8') as stream:
                for line in stream:
                    document=json.loads(line)
                    database.execute('insert into restored_documents values (?,?)',(document['name'],line))
                    rehydrated=database.execute('select wire_json from restored_documents where name=?',(document['name'],)).fetchone()[0]
                    if json.loads(rehydrated)!=document:raise ReadFailure('Offline document restore mismatch')
                    count+=1
        expected=self.manifest['stages'].get('firestore',{}).get('result',{}).get('documents')
        if expected is not None and count!=expected:raise ReadFailure('Offline document restore count mismatch')
        database.close()
        for path in (self.output/'secrets').glob('*.private.json'):
            secrets=json.loads(path.read_bytes())
            for secret in secrets:
                for version in secret.get('versions',[]):
                    if version.get('recovery_file'):
                        raw=(self.output/version['recovery_file']).read_bytes()
                        if raw!=base64.b64decode(version['recovery_payload']['data']):
                            raise ReadFailure('Plaintext secret recovery mismatch')
        for path in (self.output/'configuration').glob('*.json'):
            json.loads(path.read_bytes())
        return {'firestore_documents_rehydrated':count,'plaintext_secret_read_verified':True,'cloud_restore_executed':False}

    def run(self) -> int:
        self.manifest['status']='incomplete'
        self.save()
        for name in ['local','firestore','firestore_settings','storage','configuration','secrets','drive','logs','registry']:
            # Failed coverage must remain retryable, even after a stage copied
            # its readable files. Never promote a partial capture to success.
            legacy_categories={'drive':{'drive_archive','drive_native_file'},
                               'storage':{'soft_deleted_object'},
                               'registry':{'deployed_image_unavailable','non_docker_repository'}}
            def relevant(gap: dict[str, Any]) -> bool:
                return gap.get('stage')==name or gap.get('category') in legacy_categories.get(name,set())
            has_gaps=any(relevant(gap) for gap in self.manifest['gaps'])
            if self.manifest['stages'].get(name,{}).get('status')=='success' and not has_gaps:
                for filename,checksum in self.manifest['stages'][name].get('files',{}).items():
                    path=self.output/filename
                    if not path.is_file() or sha256(path)!=checksum:
                        raise ReadFailure('Resume checkpoint checksum mismatch')
                continue
            self.manifest['gaps']=[gap for gap in self.manifest['gaps'] if not relevant(gap)]
            gap_start=len(self.manifest['gaps'])
            self.manifest['stages'][name]={'status':'running'};self.save()
            print('Backup stage:',name,flush=True)
            try:
                result=getattr(self,name)()
                def belongs(path: Path) -> bool:
                    relative=path.relative_to(self.output)
                    if relative.parts[0]!=('firestore' if name=='firestore_settings' else name):return False
                    settings=path.name.endswith(('-indexes.json','-fields.json'))
                    return settings if name=='firestore_settings' else not (name=='firestore' and settings)
                files={str(p.relative_to(self.output)):sha256(p) for p in self.output.rglob('*') if p.is_file() and belongs(p) and '.partial' not in p.name}
                for gap in self.manifest['gaps'][gap_start:]:gap['stage']=name
                self.manifest['stages'][name]={'status':'partial' if len(self.manifest['gaps'])>gap_start else 'success','result':result,'files':files}
            except Exception as exc:
                self.manifest['stages'][name]={'status':'failed','error':str(exc) if isinstance(exc,ReadFailure) else type(exc).__name__}
                self.save();print('Backup stage failed:',name,type(exc).__name__,flush=True);return 1
            self.save()
        self.manifest['offline_restore_verification']=self.verify_local_restore()
        self.manifest['files']={str(p.relative_to(self.output)):{'bytes':p.stat().st_size,'sha256':sha256(p)} for p in self.output.rglob('*') if p.is_file() and p not in [self.manifest_path,self.output/'summary.safe.json'] and p.suffix!='.partial'}
        self.manifest['status']='complete' if not self.manifest['gaps'] else 'incomplete'
        self.manifest['finished_at']=datetime.now(timezone.utc).isoformat();self.save()
        summary={'status':self.manifest['status'],'remote_writes':self.manifest.get('remote_writes',0),'files':len(self.manifest['files']),
                 'bytes':sum(v['bytes'] for v in self.manifest['files'].values()),
                 'gaps':self.manifest['gaps'],'stages':{k:{kk:vv for kk,vv in v.get('result',{}).items() if kk!='source_revision'} for k,v in self.manifest['stages'].items()}}
        self.write('summary.safe.json',summary)
        print(json.dumps(summary,ensure_ascii=True),flush=True)
        return 0 if self.manifest['status']=='complete' else 2


def main() -> int:
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--verify-only',action='store_true');parser.add_argument('--recover-required-images',action='store_true');args=parser.parse_args()
    if args.verify_only:
        root=args.output.resolve()
        manifest=json.loads((root/'manifest.private.json').read_text(encoding='utf-8'))
        for relative,expected in manifest['files'].items():
            path=(root/relative).resolve()
            if not path.is_relative_to(root) or not path.is_file() or sha256(path)!=expected['sha256']:
                raise ReadFailure('Offline backup checksum validation failed')
        local=Backup.__new__(Backup);local.output=root;local.manifest=manifest
        verification=local.verify_local_restore()
        verification['status']=manifest['status']
        verification['gaps']=len(manifest['gaps'])
        print(json.dumps(verification))
        return 0 if manifest['status']=='complete' else 2
    return Backup(args.output,recover_required_images=args.recover_required_images).run()


if __name__=='__main__':
    raise SystemExit(main())
