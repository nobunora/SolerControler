param([AllowNull()][System.Collections.IDictionary]$Proof)

$ErrorActionPreference = 'Stop'
if ($null -eq $Proof -or $Proof['status'] -ne 'passed' -or
    $Proof['roundtrip_forced_proof'] -ne 'passed' -or
    $Proof['roundtrip_economy_proof'] -ne 'passed' -or
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
foreach ($phase in @('forced', 'economy')) {
    $fields = if ($phase -eq 'forced') { @('batteryOperatingMode') } else { @('batteryOperatingMode', 'socEconomyMode') }
    $candidates = if ($phase -eq 'forced') { @('BatteryOperatingMode') } else { @('BatteryOperatingMode', 'SocEconomyMode') }
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
Write-Host 'Device evidence accepted: forced + economy requested/observed values and exact restoration.'
