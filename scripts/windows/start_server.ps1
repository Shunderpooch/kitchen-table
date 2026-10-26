# Serve the built app on the home network (http://<this-pc>:3000).
# Rebuilds first when the code changed since the last build. Needs APP_PASSWORD in frontend\.env.
$ErrorActionPreference = "Stop"
$frontend = Join-Path $PSScriptRoot "..\..\frontend" | Resolve-Path
Set-Location $frontend
if (-not (Select-String -Path .env -Pattern '^APP_PASSWORD=.+' -Quiet)) {
    throw "Set APP_PASSWORD in frontend\.env before serving the app on the network."
}
$log = Join-Path $frontend "..\data\server.log"
$built = if (Test-Path .next\BUILD_ID) { (Get-Item .next\BUILD_ID).LastWriteTime } else { [datetime]::MinValue }
$changed = Get-ChildItem src, package.json, next.config.js -Recurse -File |
    Where-Object { $_.LastWriteTime -gt $built } | Select-Object -First 1
if ($changed) {
    npm run build *>> $log
    if ($LASTEXITCODE -ne 0) { throw "Build failed; see $log" }
}
npx next start -H 0.0.0.0 -p 3000 *>> $log
