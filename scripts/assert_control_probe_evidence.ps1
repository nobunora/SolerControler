param([AllowNull()][System.Collections.IDictionary]$Proof, [switch]$RequireController)

$ErrorActionPreference = 'Stop'
if ($null -eq $Proof -or $Proof['status'] -ne 'passed' -or
    $Proof['roundtrip_forced_proof'] -ne 'passed' -or
    $Proof['roundtrip_green_proof'] -ne 'passed' -or
    $Proof['roundtrip_restore_verified'] -ne $true) {
    throw 'Missing or failed device-level dual-profile evidence; release is blocked.'
}
$evidence = $Proof['settings_roundtrip_evidence']
if ($null -eq $evidence -or $evidence['status'] -ne 'passed' -or
    $evidence['restore_verified'] -ne $true -or
    $evidence['mutation_outcome'] -eq 'unknown' -or
    $evidence['hold_seconds'] -ne 60) {
    throw 'Incomplete settings round-trip evidence; release is blocked.'
}
foreach ($phase in @('forced', 'green')) {
    $fields = @('batteryOperatingMode')
    $candidates = @('BatteryOperatingMode')
    if ($evidence["${phase}_proof"] -ne 'passed' -or
        -not $evidence["${phase}_operation_id"] -or
        (Compare-Object $candidates @($evidence["${phase}_candidate_maps_fetched"]))) {
        throw "Invalid $phase candidate or operation evidence; release is blocked."
    }
    $changed = @($evidence["${phase}_changed_fields"])
    if ($changed -notcontains 'batteryOperatingMode' -or @($changed | Where-Object { $_ -notin $fields }).Count -gt 0) {
        throw "Invalid $phase changed fields; release is blocked."
    }
    foreach ($field in $fields) {
        $requested = $evidence["${phase}_requested"][$field]
        $observed = $evidence["${phase}_observed"][$field]
        if ($null -eq $requested -or $null -eq $observed -or
            [string]$requested -ne [string]$observed -or
            $field -notin @($evidence["${phase}_readback_fields"])) {
            throw "Missing or mismatched $phase readback; release is blocked."
        }
    }
}
Write-Host 'Device evidence accepted: forced + green requested/observed values and exact restoration.'
if ($RequireController) {
    $controller = $Proof['controller_probe']
    if ($null -eq $controller -or $controller['status'] -ne 'passed' -or
        $controller['target_reached'] -ne $true -or $controller['restore_verified'] -ne $true -or
        $controller['restored_field_count'] -ne 14 -or $controller['storage_scope'] -ne 'control_live_probes' -or
        $controller['mutation_outcome'] -eq 'unknown') {
        throw 'Extended controller storage/target/restoration proof missing or failed.'
    }
    foreach ($name in @('generated_plan', 'monitor_plan')) {
        if ($controller[$name]['status'] -ne 'verified' -or
            $controller[$name]['detail_sha256'] -notmatch '^[0-9a-f]{64}$' -or
            -not $controller[$name]['raw_gzip_base64']) {
            throw "Extended controller archive proof missing: $name"
        }
    }
    $writes = @($controller['writes'])
    if ($writes.Count -ne 2) { throw 'Expected one forced and one standby controller write.' }
    for ($i = 0; $i -lt 2; $i++) {
        $expected = @('3', '5')[$i]
        if ([string]$writes[$i]['requested'] -ne $expected -or [string]$writes[$i]['observed'] -ne $expected -or
            (Compare-Object @('batteryOperatingMode') @($writes[$i]['changed_fields']))) {
            throw 'Controller SET/read-back proof is incomplete.'
        }
    }
    $readings = @($controller['readings'])
    if ($readings.Count -lt 2 -or $readings[-1]['source'] -ne 'realtime' -or
        $null -eq $readings[-1]['soc'] -or [double]$readings[-1]['soc'] -lt [double]$controller['target_soc']) {
        throw 'Target stop lacks a real SOC observation.'
    }
    Write-Host 'Controller evidence accepted: archive/index, real SOC, forced/standby and restoration.'
}
