param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$target = Join-Path $repoRoot ".runtime\mcp-sdk"
$requirements = Join-Path $repoRoot "requirements-mcp.txt"

New-Item -ItemType Directory -Force -Path $target | Out-Null
& $Python -m pip install --upgrade --target $target -r $requirements
if ($LASTEXITCODE -ne 0) {
    throw "Could not install the isolated MCP SDK runtime."
}

Write-Output "Installed the MCP SDK helper runtime at .runtime/mcp-sdk."
