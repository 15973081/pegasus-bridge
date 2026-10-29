[CmdletBinding()]
param(
    [string]$WheelPath,
    [switch]$RegisterCodex,
    [switch]$AllowLogAuth
)
$ErrorActionPreference = 'Stop'
$packageVersion = '0.15.0'
$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCommand) {
    throw 'uv is required. Install uv first: https://docs.astral.sh/uv/getting-started/installation/'
}
if (-not $WheelPath) {
    $wheelName = "pegasus_bridge-$packageVersion-py3-none-any.whl"
    $candidates = @((Join-Path $PSScriptRoot $wheelName), (Join-Path $PSScriptRoot "../dist/$wheelName"))
    $WheelPath = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if (-not $WheelPath) { throw 'Wheel not found. Extract the complete installation ZIP, or run uv build in the source repository.' }
}
$resolvedWheel = (Resolve-Path -LiteralPath $WheelPath).Path
if ([IO.Path]::GetExtension($resolvedWheel) -ne '.whl') { throw 'WheelPath must point to a .whl file.' }
$requirement = $resolvedWheel + '[preview,qa]'
& $uvCommand.Source tool install --python 3.12 $requirement
if ($LASTEXITCODE -ne 0) { throw 'Installation failed. Existing conflicting commands were not overwritten.' }
$toolBin = (& $uvCommand.Source tool dir --bin).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot locate uv tool executables.' }
$cli = Join-Path $toolBin 'pegasus.exe'
$server = Join-Path $toolBin 'pegasus-mcp.exe'
if (-not (Test-Path -LiteralPath $cli) -or -not (Test-Path -LiteralPath $server)) {
    throw 'Installed entry points are missing.'
}
& $server --version
if ($LASTEXITCODE -ne 0) { throw 'MCP executable check failed.' }
& $cli doctor-mcp
if ($LASTEXITCODE -ne 0) { throw 'Installed MCP protocol check failed; client configuration was not changed.' }
if ($RegisterCodex) {
    $codexCommand = Get-Command codex.cmd, codex.exe, codex -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $codexCommand) { throw 'Codex CLI not found. Run pegasus mcp-config to obtain configuration for your client.' }
    $savedErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $existing = & $codexCommand.Source mcp get pegasus-editor --json 2>&1
        $getExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $savedErrorPreference
    }
    if ($getExitCode -eq 0) {
        Write-Host 'pegasus-editor is already configured; its settings were preserved. Use pegasus mcp-config to review the installed entry point.'
    } else {
        if (($existing | Out-String) -notmatch "No MCP server named 'pegasus-editor' found") {
            throw 'Cannot inspect Codex configuration. The installation is complete, but registration was not attempted.'
        }
        $logAuth = if ($AllowLogAuth) { '1' } else { '0' }
        & $codexCommand.Source mcp add pegasus-editor --env "PEGASUS_ALLOW_LOG_AUTH=$logAuth" -- $server
        if ($LASTEXITCODE -ne 0) { throw 'MCP installed, but Codex registration failed.' }
    }
}
Write-Host "Installed MCP command: $server"
Write-Host 'Use pegasus mcp-config --client json or --client codex for other client configuration.'
Write-Host 'This installs a standard stdio MCP server. It does not publish a plugin to a marketplace.'
