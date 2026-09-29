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

$verify = @'
import importlib.metadata as metadata
import pathlib
import sys

target = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(target))
expected = {"mcp": "2.2.0", "httpx2": "2.13.1", "httpcore2": "2.13.1"}
installed = {
    dist.metadata["Name"].lower(): dist.version
    for dist in metadata.distributions(path=[str(target)])
}
for package, version in expected.items():
    if installed.get(package) != version:
        raise SystemExit(f"MCP runtime verification failed for {package}.")
import mcp
import httpx2
import httpcore2

if not all((mcp.__file__, httpx2.__file__, httpcore2.__file__)):
    raise SystemExit("MCP runtime import verification failed.")
print("Verified isolated MCP 2.2.0, httpx2 2.13.1, and httpcore2 2.13.1 imports.")
'@
& $Python -c $verify $target
if ($LASTEXITCODE -ne 0) {
    throw "The isolated MCP SDK runtime failed version or import verification."
}

Write-Output "Installed the MCP SDK helper runtime at .runtime/mcp-sdk."
