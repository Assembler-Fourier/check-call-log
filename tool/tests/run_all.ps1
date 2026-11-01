# Run every check, one after another, each with a time limit; output goes to files in %TEMP%\ccl-tests (never to a
# pipe a window could hold open). The window checks park the window off-screen and never take the keyboard.
#   powershell -NoProfile -File run_all.ps1            all of them
#   powershell -NoProfile -File run_all.ps1 t_notes    just one
param([string[]]$Tests = @('t10', 't_notes', 't_script', 't_mutate', 't_link', 't_green', 't_auto', 't_ux', 'cases6', 'smoke6', 'cases'))
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$work = Join-Path $env:TEMP 'ccl-tests'; New-Item -ItemType Directory -Force $work | Out-Null
foreach ($t in $Tests) {
    $log = Join-Path $work "$t.log"; $err = Join-Path $work "$t.err"
    $p = Start-Process python -ArgumentList "-B $t.py" -WorkingDirectory $here -RedirectStandardOutput $log -RedirectStandardError $err -WindowStyle Hidden -PassThru
    if (-not $p.WaitForExit(300000)) { Stop-Process -Id $p.Id -Force; "${t}: stopped after 5 minutes" }
    "${t}: " + ((Get-Content $log | Where-Object { $_ -match 'checks passed|FAIL' }) -join ' | ')
    Get-Content $err -ErrorAction SilentlyContinue | Where-Object { $_ -notmatch 'tick"|"after" script|while executing|invalid command name' } | Select-Object -Last 6
}
