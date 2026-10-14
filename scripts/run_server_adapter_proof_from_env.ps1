param(
    [Parameter(Mandatory = $true)][string]$ImmutablePlannerImage,
    [Parameter(Mandatory = $true)][string]$FixturePath,
    [Parameter(Mandatory = $true)][string]$ReceiptPath,
    [string]$ProbeJobName = 'solar-battery-settings-roundtrip'
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot
. (Join-Path $PSScriptRoot 'production_env.ps1')
Import-ProductionEnv
$project = Get-RequiredProductionEnv 'GCP_PROJECT_ID'
$region = Get-RequiredProductionEnv 'GCP_REGION'
$gcloud = Join-Path $PSScriptRoot 'gcloud.ps1'
if ($ImmutablePlannerImage -notmatch '@sha256:[a-f0-9]{64}$') { throw 'Server proof requires a fixed planner digest.' }
if ($ProbeJobName -ne 'solar-battery-settings-roundtrip') { throw 'Only the existing dedicated probe job may be used for adapter proof.' }
$FixturePath = [IO.Path]::GetFullPath($FixturePath, $repoRoot)
$ReceiptPath = [IO.Path]::GetFullPath($ReceiptPath, $repoRoot)
$privateDirectory = "$ReceiptPath.d"
New-Item -ItemType Directory -Force -Path $privateDirectory | Out-Null

function Invoke-ProofCloud([string[]]$Arguments) {
    $output = (& $gcloud @Arguments 2>&1) -join "`n"
    $output | Set-Content -LiteralPath (Join-Path $privateDirectory 'last-cloud-response.private.txt') -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw 'Server proof cloud operation failed; inspect the private proof state.' }
    return $output
}
function Assert-ProofIdle([string]$Job) {
    $executions = @(Invoke-ProofCloud @('run', 'jobs', 'executions', 'list', '--job', $Job, '--project', $project,
        '--region', $region, '--limit', '10', '--sort-by', '~createTime', '--format', 'json') | ConvertFrom-Json -AsHashtable)
    foreach ($execution in $executions) {
        if (-not $execution.status -or -not $execution.status.completionTime) { throw 'An execution is active or unknown; server proof is blocked.' }
    }
}
foreach ($job in @($ProbeJobName, 'solar-battery-23', 'solar-battery-03', 'solar-battery-07', 'solar-forecast-daily', 'solar-drive-backup')) { Assert-ProofIdle $job }
$before = Invoke-ProofCloud @('run', 'jobs', 'describe', $ProbeJobName, '--project', $project, '--region', $region, '--format', 'json') | ConvertFrom-Json -AsHashtable
$previous = $before.spec.template.spec.template.spec.containers[0]
if (@($before.spec.template.spec.template.spec.containers).Count -ne 1) { throw 'Dedicated proof requires exactly one container.' }
$before | ConvertTo-Json -Depth 40 | Set-Content -LiteralPath (Join-Path $privateDirectory 'job-before.private.json') -Encoding utf8
if (-not $previous.image) { throw 'Dedicated job container is unknown.' }
$fixture = Get-Content -Raw -LiteralPath $FixturePath | ConvertFrom-Json -AsHashtable
if ($fixture.kind -ne 'production_server') { throw 'This wrapper requires an approved actual-server fixture.' }
& python (Join-Path $repoRoot 'tests/g07/adapter_proof.py') --fixture $FixturePath --stage preflight
if ($LASTEXITCODE -ne 0) { throw 'Owned production-server fixture failed preflight.' }
$bundlePath = Join-Path $privateDirectory 'proof-bundle.zip'
& python -c 'import zipfile,sys,pathlib;root=pathlib.Path(sys.argv[1]);z=zipfile.ZipFile(sys.argv[3],"w",compression=zipfile.ZIP_DEFLATED);[z.write(root/name,name) for name in ("adapter_proof.py","firestore_server_cases.py","gcs_server_cases.py")];z.write(sys.argv[2],"fixture.json");z.close()' (Join-Path $repoRoot 'tests/g07') $FixturePath $bundlePath
if ($LASTEXITCODE -ne 0) { throw 'Fixed server proof bundle could not be built.' }
$bundleSha = (Get-FileHash -LiteralPath $bundlePath -Algorithm SHA256).Hash.ToLowerInvariant()
$bundleName = "$($fixture.prefix)bundle-$bundleSha.zip"
$bundleInfo = (& python -c 'import sys,json,hashlib;from google.cloud.storage import Client;from google.api_core.exceptions import PreconditionFailed;b=Client().bucket(sys.argv[1]).blob(sys.argv[2]);raw=open(sys.argv[3],"rb").read();created=True
try:b.upload_from_string(raw,if_generation_match=0,timeout=4,retry=None)
except PreconditionFailed:created=False
b.reload(timeout=4,retry=None);generation=int(b.generation);assert b.download_as_bytes(if_generation_match=generation,timeout=4,retry=None)==raw;print(json.dumps({"generation":generation,"created":created}))' $fixture.bucket $bundleName $bundlePath) -join "`n"
if ($LASTEXITCODE -ne 0) { throw 'Fixed proof bundle upload/readback failed.' }
$bundle = $bundleInfo | ConvertFrom-Json -AsHashtable
$bundleUri = "gs://$($fixture.bucket)/$bundleName"
$imageDigest = ($ImmutablePlannerImage -split '@')[-1]
$loader = @'
import sys,zipfile,tempfile,pathlib,hashlib,runpy,json
from urllib.parse import urlparse
from google.cloud.storage import Client
u=urlparse(sys.argv[1]);blob=Client().bucket(u.netloc).blob(u.path.lstrip('/'))
raw=blob.download_as_bytes(if_generation_match=int(sys.argv[3]),timeout=4,retry=None)
assert hashlib.sha256(raw).hexdigest()==sys.argv[2]
with tempfile.TemporaryDirectory(prefix='adapter-proof-') as d:
 p=pathlib.Path(d);bundle=p/'bundle.zip';bundle.write_bytes(raw)
 with zipfile.ZipFile(bundle) as z:
  assert set(z.namelist())=={'adapter_proof.py','firestore_server_cases.py','gcs_server_cases.py','fixture.json'}
  z.extractall(p)
 sys.path.insert(0,str(p));sys.path.insert(0,'/app')
 sys.argv=[str(p/'adapter_proof.py'),'--fixture',str(p/'fixture.json'),'--stage','all','--image-digest',sys.argv[4],'--receipt',str(p/'receipt.json')]
 try:runpy.run_path(str(p/'adapter_proof.py'),run_name='__main__')
 except SystemExit as e:code=e.code
 receipt=json.loads((p/'receipt.json').read_text());print(json.dumps({'event':'server_adapter_proof','receipt':receipt}),flush=True)
 sys.exit(code)
'@
$flagsPath = Join-Path $privateDirectory 'proof-update.private.json'
@{ '--image' = $ImmutablePlannerImage; '--command' = 'python'; '--args' = @('-c', $loader, $bundleUri, $bundleSha, [string]$bundle.generation, $imageDigest) } |
    ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $flagsPath -Encoding utf8
$attemptedUpdate = $false
$restored = $false
$proofError = $null
function Read-ExactProofReceipt([string]$ExecutionName, [string]$Filter, [datetime]$Deadline) {
    # Cloud Run can finish before its structured receipt is searchable. Retry
    # only this read; never repeat the completed execution or its SDK writes.
    while ([DateTime]::UtcNow -lt $Deadline) {
        $records = @(Invoke-ProofCloud @('logging', 'read', $Filter, '--project', $project, '--freshness', '1d', '--limit', '200', '--format', 'json') | ConvertFrom-Json -AsHashtable)
        ConvertTo-Json -InputObject $records -Depth 40 | Set-Content -LiteralPath (Join-Path $privateDirectory 'proof-records.private.json') -Encoding utf8
        $proofs = @($records | Where-Object {
            $_ -is [System.Collections.IDictionary] -and $_.Contains('labels') -and
            $_.labels -is [System.Collections.IDictionary] -and $_.labels.Contains('run.googleapis.com/execution_name') -and
            $_.labels['run.googleapis.com/execution_name'] -eq $ExecutionName -and
            $_.Contains('jsonPayload') -and $_.jsonPayload -is [System.Collections.IDictionary] -and
            $_.jsonPayload.Contains('event') -and $_.jsonPayload.event -eq 'server_adapter_proof'
        })
        if ($proofs.Count -gt 1) { throw 'Exact execution proof is ambiguous.' }
        if ($proofs.Count -eq 1) { return $proofs[0].jsonPayload.receipt }
        if ([DateTime]::UtcNow -lt $Deadline) { Start-Sleep -Milliseconds 1000 }
    }
    throw 'Exact execution proof was not available within the read budget.'
}

function Assert-ProofTerminal([hashtable]$Execution) {
    if (-not $Execution.status -or -not $Execution.status.completionTime) { throw 'Native proof execution is not terminal.' }
    foreach ($type in @('Completed', 'ResourcesAvailable', 'Started', 'ContainerReady')) {
        $condition = @($Execution.status.conditions | Where-Object { $_.type -eq $type })
        if ($condition.Count -ne 1 -or [string]$condition[0].status -ne 'True') { throw 'Native proof execution readiness is not verified.' }
    }
    $tasks = $Execution.spec.taskCount
    $succeeded = $Execution.status.succeededCount
    if (($tasks -isnot [int] -and $tasks -isnot [long]) -or $tasks -lt 1 -or
        ($succeeded -isnot [int] -and $succeeded -isnot [long]) -or $succeeded -ne $tasks) {
        throw 'Native proof total-task success evidence is incomplete.'
    }
    foreach ($key in @('failedCount', 'cancelledCount', 'pendingCount', 'runningCount')) {
        if ($Execution.status.ContainsKey($key)) {
            $count = $Execution.status[$key]
            if (($count -isnot [int] -and $count -isnot [long]) -or $count -ne 0) { throw 'Native proof task outcome is invalid or unfinished.' }
        }
    }
}
try {
    $attemptedUpdate = $true
    Invoke-ProofCloud @('run', 'jobs', 'update', $ProbeJobName, '--project', $project, '--region', $region, '--flags-file', $flagsPath) | Out-Null
    $candidate = Invoke-ProofCloud @('run', 'jobs', 'describe', $ProbeJobName, '--project', $project, '--region', $region, '--format', 'json') | ConvertFrom-Json -AsHashtable
    $container = $candidate.spec.template.spec.template.spec.containers[0]
    if ($container.image -ne $ImmutablePlannerImage -or ($container.command -join ',') -ne 'python' -or
        ($container.args | ConvertTo-Json -Compress) -ne (@('-c', $loader, $bundleUri, $bundleSha, [string]$bundle.generation, $imageDigest) | ConvertTo-Json -Compress)) { throw 'Candidate proof entrypoint readback mismatch.' }
    $executionText = (& $gcloud run jobs execute $ProbeJobName --project $project --region $region --update-env-vars "PLAN_SOURCE_REVISION=$($fixture.source_commit),PLAN_IMAGE_DIGEST=$imageDigest" --wait --format json) -join "`n"
    $executionExit = $LASTEXITCODE
    $executionText | Set-Content -LiteralPath (Join-Path $privateDirectory 'execution.private.json') -Encoding utf8
    $execution = $executionText | ConvertFrom-Json -AsHashtable
    $executionName = ([string]$execution.metadata.name -split '/')[-1]
    if (-not $executionName) { throw 'Proof execution identity is unknown.' }
    $exactText = Invoke-ProofCloud @('run', 'jobs', 'executions', 'describe', $executionName, '--project', $project,
        '--region', $region, '--format', 'json')
    $exactExecution = $exactText | ConvertFrom-Json -AsHashtable
    $exactText | Set-Content -LiteralPath (Join-Path $privateDirectory 'execution-exact.private.json') -Encoding utf8
    if (([string]$exactExecution.metadata.name -split '/')[-1] -ne $executionName) { throw 'Proof execution identity changed.' }
    Assert-ProofTerminal $exactExecution
    $filter = ('resource.type=cloud_run_job AND resource.labels.job_name="' + $ProbeJobName +
        '" AND labels."run.googleapis.com/execution_name"="' + $executionName + '" AND jsonPayload.event="server_adapter_proof"').Replace('"', '\"')
    $proof = Read-ExactProofReceipt $executionName $filter ([DateTime]::UtcNow.AddSeconds(60))
    $proof | ConvertTo-Json -Depth 40 | Set-Content -LiteralPath $ReceiptPath -Encoding utf8
    if ($proof.image_digest -ne $imageDigest -or $proof.fixture_sha256 -ne $fixture.fixture_sha256 -or
        $proof.source_commit -ne $fixture.source_commit -or $proof.runtime_plan_source_revision -ne $fixture.source_commit -or
        $proof.status -ne 'PASS' -or $executionExit -ne 0) { throw 'Real server proof remains incomplete.' }
} catch { $proofError = $_ }
finally {
    if ($attemptedUpdate) {
        $restorePath = Join-Path $privateDirectory 'restore.private.json'
        @{ '--image' = $previous.image; '--command' = ($previous.command -join ','); '--args' = @($previous.args) } |
            ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $restorePath -Encoding utf8
        try {
            Assert-ProofIdle $ProbeJobName
            Invoke-ProofCloud @('run', 'jobs', 'update', $ProbeJobName, '--project', $project, '--region', $region, '--flags-file', $restorePath) | Out-Null
            $after = Invoke-ProofCloud @('run', 'jobs', 'describe', $ProbeJobName, '--project', $project, '--region', $region, '--format', 'json') | ConvertFrom-Json -AsHashtable
            $actual = $after.spec.template.spec.template.spec.containers[0]
            $restored = $actual.image -eq $previous.image -and ($actual.command | ConvertTo-Json -Compress) -eq ($previous.command | ConvertTo-Json -Compress) -and
                ($actual.args | ConvertTo-Json -Compress) -eq ($previous.args | ConvertTo-Json -Compress)
            $restored = $restored -and ($actual.env | ConvertTo-Json -Depth 40 -Compress) -eq ($previous.env | ConvertTo-Json -Depth 40 -Compress) -and
                ($actual.resources | ConvertTo-Json -Depth 40 -Compress) -eq ($previous.resources | ConvertTo-Json -Depth 40 -Compress)
        } catch { $restored = $false }
    }
    if ($bundle.created -and $restored) {
        & python -c 'import sys;from google.cloud.storage import Client;Client().bucket(sys.argv[1]).blob(sys.argv[2]).delete(if_generation_match=int(sys.argv[3]),timeout=4,retry=None)' $fixture.bucket $bundleName ([string]$bundle.generation)
        if ($LASTEXITCODE -ne 0) { throw 'Owned proof bundle cleanup is unconfirmed.' }
    }
}
if (-not $restored) { throw 'Dedicated job restoration is UNKNOWN; do not retry or start a physical probe.' }
if ($proofError) { throw 'Real-server proof failed or is incomplete; original dedicated job was restored.' }
