param(
    [string]$Service = "Compute Engine",
    [string]$ServiceId = "",
    [string]$Region = "",
    [string]$CurrencyCode = "USD",
    [int]$Top = 20,
    [switch]$ListServices,
    [switch]$All,
    [switch]$AsJson
)

$ErrorActionPreference = "Stop"

if ($Top -lt 1) {
    throw "-Top must be 1 or greater."
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

function Convert-ToQueryString {
    param(
        [hashtable]$Query
    )

    if (-not $Query -or $Query.Count -eq 0) {
        return ""
    }

    $pairs = @()
    foreach ($key in $Query.Keys) {
        $value = $Query[$key]
        if ($null -eq $value) {
            continue
        }
        $text = [string]$value
        if ([string]::IsNullOrWhiteSpace($text)) {
            continue
        }
        $pairs += ("{0}={1}" -f [uri]::EscapeDataString([string]$key), [uri]::EscapeDataString($text))
    }

    if ($pairs.Count -eq 0) {
        return ""
    }
    return "?" + ($pairs -join "&")
}

function Invoke-PricingApi {
    param(
        [string]$Path,
        [hashtable]$Query = @{}
    )

    $token = (Invoke-GCloud auth print-access-token).Trim()
    if (-not $token) {
        throw "Failed to get access token from gcloud."
    }

    $uri = "https://cloudbilling.googleapis.com/v1/$Path$(Convert-ToQueryString -Query $Query)"
    Invoke-RestMethod -Method Get -Headers @{ Authorization = "Bearer $token" } -Uri $uri
}

function Get-AllPages {
    param(
        [string]$Path,
        [string]$CollectionName,
        [hashtable]$BaseQuery = @{},
        [int]$PageSize = 5000
    )

    $items = @()
    $pageToken = ""
    do {
        $query = @{}
        foreach ($key in $BaseQuery.Keys) {
            $query[$key] = $BaseQuery[$key]
        }
        $query["pageSize"] = $PageSize
        if ($pageToken) {
            $query["pageToken"] = $pageToken
        }

        $response = Invoke-PricingApi -Path $Path -Query $query
        $newItems = $response.$CollectionName
        if ($newItems) {
            $items += @($newItems)
        }
        $pageToken = [string]$response.nextPageToken
    } while ($pageToken)

    return $items
}

function Resolve-Service {
    param(
        [object[]]$Services,
        [string]$ServiceName,
        [string]$SelectedServiceId
    )

    if ($SelectedServiceId) {
        $byId = $Services | Where-Object { $_.serviceId -eq $SelectedServiceId } | Select-Object -First 1
        if (-not $byId) {
            throw "ServiceId '$SelectedServiceId' not found. Run with -ListServices."
        }
        return $byId
    }

    $matches = $Services | Where-Object { $_.displayName -like "*$ServiceName*" }
    if (-not $matches) {
        throw "No service matched '$ServiceName'. Run with -ListServices."
    }

    $exactName = $matches | Where-Object { $_.displayName -eq $ServiceName } | Select-Object -First 1
    if ($exactName) {
        return $exactName
    }

    $selected = $matches | Sort-Object displayName | Select-Object -First 1
    if (@($matches).Count -gt 1) {
        Write-Warning "Multiple services matched '$ServiceName'. Using '$($selected.displayName)' [$($selected.serviceId)]."
    }
    return $selected
}

function Convert-UnitPriceToDecimal {
    param(
        [object]$UnitPrice
    )

    if ($null -eq $UnitPrice) {
        return [decimal]0
    }

    $units = [decimal]0
    if (
        $UnitPrice.PSObject.Properties.Name -contains "units" -and
        $null -ne $UnitPrice.units -and
        [string]$UnitPrice.units -ne ""
    ) {
        $units = [decimal]::Parse([string]$UnitPrice.units, [System.Globalization.CultureInfo]::InvariantCulture)
    }

    $nanos = [decimal]0
    if ($UnitPrice.PSObject.Properties.Name -contains "nanos" -and $null -ne $UnitPrice.nanos) {
        $nanos = ([decimal]$UnitPrice.nanos) / [decimal]1000000000
    }

    return ($units + $nanos)
}

function Format-TieredRates {
    param(
        [object[]]$TieredRates
    )

    if (-not $TieredRates -or $TieredRates.Count -eq 0) {
        return ""
    }

    $lines = @()
    foreach ($tier in $TieredRates) {
        $start = "0"
        if ($tier.PSObject.Properties.Name -contains "startUsageAmount" -and $null -ne $tier.startUsageAmount) {
            $start = [string]$tier.startUsageAmount
        }
        $price = Convert-UnitPriceToDecimal -UnitPrice $tier.unitPrice
        $priceText = [string]::Format(
            [System.Globalization.CultureInfo]::InvariantCulture,
            "{0:0.##########}",
            $price
        )
        $lines += ("{0}+ => {1}" -f $start, $priceText)
    }

    return ($lines -join "; ")
}

Write-Host "Loading service catalog from Cloud Billing API..."
$services = Get-AllPages -Path "services" -CollectionName "services"

if ($ListServices) {
    $filtered = $services
    if ($Service) {
        $filtered = $services | Where-Object { $_.displayName -like "*$Service*" }
    }
    if (-not $filtered) {
        Write-Warning "No services matched '$Service'."
        exit 0
    }
    $filtered |
        Sort-Object displayName |
        Select-Object serviceId, displayName |
        Format-Table -AutoSize
    exit 0
}

$resolvedService = Resolve-Service -Services $services -ServiceName $Service -SelectedServiceId $ServiceId
Write-Host "Using service: $($resolvedService.displayName) [$($resolvedService.serviceId)]"

$queryBase = @{
    currencyCode = $CurrencyCode
}

$skus = @()
$pageToken = ""
do {
    $query = @{}
    foreach ($key in $queryBase.Keys) {
        $query[$key] = $queryBase[$key]
    }
    $query["pageSize"] = 5000
    if ($pageToken) {
        $query["pageToken"] = $pageToken
    }

    $response = Invoke-PricingApi -Path ("services/{0}/skus" -f $resolvedService.serviceId) -Query $query
    $pageSkus = @($response.skus)

    if ($Region) {
        $pageSkus = $pageSkus | Where-Object {
            ($_.serviceRegions -contains $Region) -or
            ($_.geoTaxonomy -and $_.geoTaxonomy.regions -contains $Region)
        }
    }

    if ($pageSkus) {
        $skus += $pageSkus
    }

    $pageToken = [string]$response.nextPageToken

    if (-not $All -and $skus.Count -ge $Top) {
        break
    }
} while ($pageToken)

if (-not $All) {
    $skus = $skus | Select-Object -First $Top
}

$rows = foreach ($sku in $skus) {
    $pricingInfo = $null
    if ($sku.pricingInfo) {
        $pricingInfo = $sku.pricingInfo | Sort-Object effectiveTime -Descending | Select-Object -First 1
    }

    $expression = $null
    if ($pricingInfo) {
        $expression = $pricingInfo.pricingExpression
    }

    $regions = ""
    if ($sku.serviceRegions) {
        $regionList = @($sku.serviceRegions | Sort-Object)
        if ($regionList.Count -gt 5) {
            $regions = (($regionList | Select-Object -First 5) -join ",") + ",..."
        } else {
            $regions = ($regionList -join ",")
        }
    } else {
        $regions = "global"
    }

    [pscustomobject]@{
        skuId            = $sku.skuId
        description      = $sku.description
        usageType        = if ($sku.category) { $sku.category.usageType } else { "" }
        resourceGroup    = if ($sku.category) { $sku.category.resourceGroup } else { "" }
        usageUnit        = if ($expression) { $expression.usageUnit } else { "" }
        tieredUnitPrice  = if ($expression) { Format-TieredRates -TieredRates $expression.tieredRates } else { "" }
        regions          = $regions
        effectiveTimeUtc = if ($pricingInfo) { $pricingInfo.effectiveTime } else { "" }
    }
}

if ($AsJson) {
    $rows | ConvertTo-Json -Depth 8
    exit 0
}

$rows |
    Select-Object skuId, usageType, resourceGroup, usageUnit, tieredUnitPrice, regions, description, effectiveTimeUtc |
    Format-List
Write-Host ""
Write-Host ("Returned {0} SKU(s). Currency={1}" -f $rows.Count, $CurrencyCode)
if (-not $All -and $pageToken) {
    Write-Host "More results exist. Add -All to fetch all pages."
}
