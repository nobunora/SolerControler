param(
    [string]$BillingAccountId = "",
    [string]$ProjectId = "",
    [int]$TopServices = 10,
    [switch]$AsJson
)

$ErrorActionPreference = "Stop"

if ($TopServices -lt 1) {
    throw "-TopServices must be 1 or greater."
}

function Invoke-GCloud {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Args
    )
    $gcloudCmd = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
    if (-not (Test-Path $gcloudCmd)) {
        throw "gcloud.cmd not found: $gcloudCmd"
    }
    & $gcloudCmd @Args
    if ($LASTEXITCODE -ne 0) {
        throw "gcloud failed: $($Args -join ' ')"
    }
}

function Resolve-ProjectId {
    param(
        [string]$ExplicitProjectId
    )

    if ($ExplicitProjectId) {
        return $ExplicitProjectId
    }
    $resolved = (Invoke-GCloud config get-value project).Trim()
    if (-not $resolved -or $resolved -eq "(unset)") {
        throw "Project is not set. Use -ProjectId or run gcloud config set project."
    }
    return $resolved
}

function Resolve-BillingAccountId {
    param(
        [string]$ExplicitBillingAccountId,
        [string]$ResolvedProjectId
    )

    if ($ExplicitBillingAccountId) {
        return $ExplicitBillingAccountId
    }

    $json = Invoke-GCloud billing projects describe $ResolvedProjectId --format json
    $billingInfo = $json | ConvertFrom-Json
    if (-not $billingInfo.billingEnabled -or -not $billingInfo.billingAccountName) {
        throw "Billing is not enabled for project '$ResolvedProjectId'."
    }
    return ([string]$billingInfo.billingAccountName).Replace("billingAccounts/", "")
}

function Get-FinalInsightsResult {
    param(
        [string]$BillingAccount
    )

    $todayUtc = (Get-Date).ToString("yyyy-MM-dd")
    $firstDayUtc = (Get-Date -Day 1).ToString("yyyy-MM-dd")
    $token = (Invoke-GCloud auth print-access-token).Trim()
    if (-not $token) {
        throw "Failed to get access token from gcloud."
    }

    $prompts = @(
@"
For billing account $BillingAccount, return month-to-date cost for $firstDayUtc to $todayUtc in billing currency.
Include total net cost, total credits, and top services by absolute subtotal (including negative values, all services, not limited to any specific service).
Include an account-level total row with columns subtotal, total_credit, and currency_code.
"@,
@"
For billing account $BillingAccount, what is the total net cost and total credits for $firstDayUtc to $todayUtc?
Also return top services by absolute subtotal across all services.
"@,
@"
Show month-to-date billing totals for $BillingAccount from $firstDayUtc to $todayUtc in billing currency.
Return account subtotal, credits, and service-level subtotals.
"@
    )

    $lastFinalResult = $null
    foreach ($prompt in $prompts) {
        $bodyObject = @{
            prompt = $prompt
            parents = @(
                @{
                    billingAccount = "billingAccounts/$BillingAccount"
                }
            )
        }
        $bodyJson = $bodyObject | ConvertTo-Json -Depth 8 -Compress
        $tmpPath = [System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), "gcp-cost-insights-$([guid]::NewGuid().ToString('N')).json")
        try {
            Invoke-WebRequest `
                -Method Post `
                -Uri "https://cloudbilling.googleapis.com/v1beta:generateInsights" `
                -Headers @{ Authorization = "Bearer $token" } `
                -ContentType "application/json" `
                -Body $bodyJson `
                -OutFile $tmpPath
            $raw = Get-Content -Path $tmpPath -Raw -Encoding UTF8
            $events = $raw | ConvertFrom-Json
            if (-not $events) {
                continue
            }
            $finalEvent = $events | Where-Object { $_.PSObject.Properties.Name -contains "finalResult" } | Select-Object -Last 1
            if (-not $finalEvent -or -not $finalEvent.finalResult) {
                continue
            }
            $lastFinalResult = $finalEvent.finalResult

            $hasRows = $false
            foreach ($dataSet in @($lastFinalResult.dataSets)) {
                if ($dataSet.billingData -and $dataSet.billingData.rows -and @($dataSet.billingData.rows).Count -gt 0) {
                    $hasRows = $true
                    break
                }
            }
            if ($hasRows) {
                return $lastFinalResult
            }
        } catch {
            continue
        } finally {
            if (Test-Path $tmpPath) {
                Remove-Item -LiteralPath $tmpPath -Force
            }
        }
    }

    if ($lastFinalResult) {
        return $lastFinalResult
    }
    throw "generateInsights did not return usable data."
}

function Convert-RowValue {
    param(
        [object]$Value
    )

    if ($null -eq $Value) {
        return $null
    }
    if ($Value.PSObject.Properties.Name -contains "stringValue") {
        return [string]$Value.stringValue
    }
    if ($Value.PSObject.Properties.Name -contains "doubleValue") {
        return [double]$Value.doubleValue
    }
    if ($Value.PSObject.Properties.Name -contains "int64Value") {
        return [double]$Value.int64Value
    }
    if ($Value.PSObject.Properties.Name -contains "boolValue") {
        return [bool]$Value.boolValue
    }
    return $Value
}

function Find-ServiceRows {
    param(
        [object]$FinalResult,
        [int]$TopN
    )

    $rows = @()
    foreach ($dataSet in @($FinalResult.dataSets)) {
        if (-not $dataSet.billingData -or -not $dataSet.billingData.columnInfo -or -not $dataSet.billingData.rows) {
            continue
        }

        $columns = @($dataSet.billingData.columnInfo | ForEach-Object { $_.column })
        $serviceIdx = [array]::IndexOf($columns, "service_display_name")
        $subtotalIdx = [array]::IndexOf($columns, "subtotal")
        if ($serviceIdx -lt 0 -or $subtotalIdx -lt 0) {
            continue
        }

        foreach ($row in @($dataSet.billingData.rows)) {
            $values = @($row.values)
            if ($values.Count -le [math]::Max($serviceIdx, $subtotalIdx)) {
                continue
            }

            $serviceName = [string](Convert-RowValue -Value $values[$serviceIdx])
            $subtotal = Convert-RowValue -Value $values[$subtotalIdx]
            if (-not $serviceName) {
                continue
            }

            $rows += [pscustomobject]@{
                service = $serviceName
                subtotal = [double]$subtotal
            }
        }
    }

    if (-not $rows) {
        return @()
    }

    $deduped = $rows |
        Group-Object service |
        ForEach-Object {
            [pscustomobject]@{
                service = $_.Name
                subtotal = ($_.Group | Measure-Object -Property subtotal -Sum).Sum
            }
        } |
        Sort-Object `
            @{ Expression = { [math]::Abs([double]$_.subtotal) }; Descending = $true }, `
            @{ Expression = { [double]$_.subtotal }; Descending = $true } |
        Select-Object -First $TopN

    return @($deduped)
}

function Find-AccountTotals {
    param(
        [object]$FinalResult
    )

    $candidates = @()
    foreach ($dataSet in @($FinalResult.dataSets)) {
        if (-not $dataSet.billingData -or -not $dataSet.billingData.columnInfo -or -not $dataSet.billingData.rows) {
            continue
        }
        $columns = @($dataSet.billingData.columnInfo | ForEach-Object { $_.column })
        $subtotalIdx = [array]::IndexOf($columns, "subtotal")
        $creditIdx = [array]::IndexOf($columns, "total_credit")
        $currencyIdx = [array]::IndexOf($columns, "currency_code")
        if ($subtotalIdx -lt 0 -or $creditIdx -lt 0 -or $currencyIdx -lt 0) {
            continue
        }
        $leafIdx = [array]::IndexOf($columns, "leaf_account_id")
        foreach ($row in @($dataSet.billingData.rows)) {
            if (-not $row) {
                continue
            }
            $values = @($row.values)
            if ($values.Count -le [math]::Max([math]::Max($subtotalIdx, $creditIdx), $currencyIdx)) {
                continue
            }
            $subtotal = [double](Convert-RowValue -Value $values[$subtotalIdx])
            $credits = [double](Convert-RowValue -Value $values[$creditIdx])
            $currency = [string](Convert-RowValue -Value $values[$currencyIdx])
            $hasLeaf = $false
            if ($leafIdx -ge 0 -and $values.Count -gt $leafIdx) {
                $leafValue = [string](Convert-RowValue -Value $values[$leafIdx])
                if ($leafValue) {
                    $hasLeaf = $true
                }
            }
            $score = [math]::Abs($subtotal) + [math]::Abs($credits)
            $candidates += [pscustomobject]@{
                subtotal = $subtotal
                totalCredit = $credits
                currencyCode = $currency
                hasLeaf = $hasLeaf
                score = $score
            }
        }
    }

    if ($candidates.Count -eq 0) {
        return $null
    }

    $best = $candidates |
        Sort-Object `
            @{ Expression = { if ($_.hasLeaf) { 1 } else { 0 } }; Descending = $true }, `
            @{ Expression = { [double]$_.score }; Descending = $true } |
        Select-Object -First 1

    return [pscustomobject]@{
        subtotal = $best.subtotal
        totalCredit = $best.totalCredit
        currencyCode = $best.currencyCode
    }
}

function Convert-NumberStringToDouble {
    param(
        [string]$Text
    )
    if (-not $Text) {
        return $null
    }
    $normalized = $Text.Replace(",", "")
    return [double]::Parse($normalized, [System.Globalization.CultureInfo]::InvariantCulture)
}

function Find-AccountTotalsFromSummary {
    param(
        [string]$Summary
    )

    if (-not $Summary) {
        return $null
    }

    $subtotalMatch = [regex]::Match(
        $Summary,
        '(?i)total\s+net\s+cost(?:\s+\(subtotal\))?(?:\s+for\s+the\s+period\s+\d{4}-\d{2}-\d{2}\s+to\s+\d{4}-\d{2}-\d{2})?(?:\s+is|\s+of)?\s+([+-]?\d[\d,]*\.?\d*)\s+([A-Z]{3})'
    )
    $creditMatch = [regex]::Match(
        $Summary,
        '(?i)total\s+credits(?:\s+(?:were|was|of|is|=|:|amounting\s+to))?\s+([+-]?\d[\d,]*\.?\d*)\s+([A-Z]{3})'
    )

    if (-not $subtotalMatch.Success -or -not $creditMatch.Success) {
        return $null
    }

    $subtotal = Convert-NumberStringToDouble -Text $subtotalMatch.Groups[1].Value
    $credits = Convert-NumberStringToDouble -Text $creditMatch.Groups[1].Value
    $currency = $subtotalMatch.Groups[2].Value
    if (-not $currency) {
        $currency = $creditMatch.Groups[2].Value
    }

    return [pscustomobject]@{
        subtotal = $subtotal
        totalCredit = $credits
        currencyCode = $currency
    }
}

$resolvedProjectId = Resolve-ProjectId -ExplicitProjectId $ProjectId
$resolvedBillingAccountId = Resolve-BillingAccountId -ExplicitBillingAccountId $BillingAccountId -ResolvedProjectId $resolvedProjectId

if (-not $AsJson) {
    Write-Host "Project: $resolvedProjectId"
    Write-Host "Billing account: $resolvedBillingAccountId"
    Write-Host "Querying month-to-date actual cost via Cloud Billing generateInsights API..."
}

$finalResult = Get-FinalInsightsResult -BillingAccount $resolvedBillingAccountId
$serviceRows = Find-ServiceRows -FinalResult $finalResult -TopN $TopServices
$totals = Find-AccountTotals -FinalResult $finalResult
if (-not $totals) {
    $totals = Find-AccountTotalsFromSummary -Summary $finalResult.summary
}

$output = [pscustomobject]@{
    projectId = $resolvedProjectId
    billingAccountId = $resolvedBillingAccountId
    totals = $totals
    summary = $finalResult.summary
    topServices = $serviceRows
    generatedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
}

if ($AsJson) {
    $output | ConvertTo-Json -Depth 8
    exit 0
}

Write-Host ""
if ($totals) {
    Write-Host ("Net subtotal: {0} {1}" -f $totals.subtotal, $totals.currencyCode)
    Write-Host ("Total credits: {0} {1}" -f $totals.totalCredit, $totals.currencyCode)
    Write-Host ""
}

if ($serviceRows.Count -gt 0) {
    Write-Host ("Top {0} services by subtotal:" -f $serviceRows.Count)
    $serviceRows |
        Select-Object service, @{
            Name = "subtotal"
            Expression = {
                [string]::Format([System.Globalization.CultureInfo]::InvariantCulture, "{0:0.#########}", [double]$_.subtotal)
            }
        } |
        Format-Table -AutoSize
} else {
    Write-Host "Top service breakdown could not be extracted from API response."
}
