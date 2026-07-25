param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Artifact
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $Artifact -PathType Leaf)) {
    throw "Authenticode target does not exist: $Artifact"
}

$certificate = $env:ENMOTION_WINDOWS_CERTIFICATE_PATH
$password = $env:ENMOTION_WINDOWS_CERTIFICATE_PASSWORD
$timestamp = $env:ENMOTION_WINDOWS_TIMESTAMP_URL
if ([string]::IsNullOrWhiteSpace($certificate) -or
    [string]::IsNullOrWhiteSpace($password) -or
    [string]::IsNullOrWhiteSpace($timestamp)) {
    throw "Windows release signing secrets are not configured"
}
if (-not (Test-Path -LiteralPath $certificate -PathType Leaf)) {
    throw "Windows signing certificate file is missing"
}
if (-not $timestamp.StartsWith("https://")) {
    throw "Windows timestamp URL must use HTTPS"
}

$signtool = (Get-Command signtool.exe -ErrorAction Stop).Source
& $signtool sign /fd SHA256 /td SHA256 /tr $timestamp /f $certificate /p $password $Artifact
if ($LASTEXITCODE -ne 0) { throw "Authenticode signing failed" }
& $signtool verify /pa /all /v $Artifact
if ($LASTEXITCODE -ne 0) { throw "Authenticode verification failed" }
