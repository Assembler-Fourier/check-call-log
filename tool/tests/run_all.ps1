# Run checks serially, with isolated scratch data and retained logs for each run.
#   powershell -NoProfile -File run_all.ps1
#   powershell -NoProfile -File run_all.ps1 -Tests t_whatsapp -WorkRoot C:\scratch\checks
param(
    [string[]]$Tests = @('t10', 't_notes', 't_script', 't_mutate', 't_link', 't_green', 't_auto', 't_auto_evidence', 't_ux', 't_layout', 'cases6', 'smoke6', 'cases', 't_whatsapp', 't_whatsapp_followups', 't_wa_beta', 't_wa_contacts', 't_wa_bridge', 't_wa_read', 't_wa_ui', 't_wa_deep', 't_wa_aliases', 't_wa_workflows', 't_wa_read_ui', 't_access', 't_harness', 't_mainloop', 't_calllog_storage', 't_persistence_ui', 't_wa_send_ui', 't_wa_cache', 't_wa_pending_ui', 't_wa_presentation', 't_wa_actual_time', 't_wa_arrival', 't_only_lena'),
    [string]$WorkRoot = (Join-Path ([System.IO.Path]::GetTempPath()) 'ccl-test-runs'),
    [ValidateRange(1, 3600)][int]$TimeoutSeconds = 300,
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
if (-not ('CCLTestJob' -as [type])) {
    # A job owns only the process we assign and its descendants. Unlike taskkill,
    # it needs no access to enumerate other users' processes, and works on PS 5.1.
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class CCLTestJob {
    [StructLayout(LayoutKind.Sequential)] struct Basic {
        public long UserTime, JobTime;
        public uint Flags;
        public UIntPtr MinWork, MaxWork;
        public uint ActiveProcesses;
        public UIntPtr Affinity;
        public uint Priority, Scheduling;
    }
    [StructLayout(LayoutKind.Sequential)] struct Io {
        public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes;
    }
    [StructLayout(LayoutKind.Sequential)] struct Extended {
        public Basic Limits;
        public Io Counters;
        public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
    }
    [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)] static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool SetInformationJobObject(IntPtr job, int kind, ref Extended info, uint size);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool TerminateJobObject(IntPtr job, uint code);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool CloseHandle(IntPtr handle);
    public static IntPtr Create() {
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero) throw new Win32Exception();
        Extended info = new Extended();
        info.Limits.Flags = 0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if (!SetInformationJobObject(job, 9, ref info, (uint)Marshal.SizeOf(info))) {
            int error = Marshal.GetLastWin32Error(); CloseHandle(job); throw new Win32Exception(error);
        }
        return job;
    }
    public static void Assign(IntPtr job, IntPtr process) {
        if (!AssignProcessToJobObject(job, process)) throw new Win32Exception();
    }
    public static void Stop(IntPtr job) {
        if (!TerminateJobObject(job, 1)) throw new Win32Exception();
    }
    public static void Close(IntPtr job) { if (job != IntPtr.Zero) CloseHandle(job); }
}
'@
}
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$run = Join-Path ([System.IO.Path]::GetFullPath($WorkRoot)) ((Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N'))
$logs = Join-Path $run 'logs'
$scratch = Join-Path $run 'scratch'
New-Item -ItemType Directory -Path $logs, $scratch -Force | Out-Null
"Test run: $run"

# These overrides are process-local and restored below; no Windows settings change.
$saved = @{}
Get-ChildItem Env: | Where-Object { $_.Name -like 'CCL_*' -or $_.Name -in @('TEMP', 'TMP', 'PYTHONDONTWRITEBYTECODE', 'PYTHONUNBUFFERED', 'PYTHONIOENCODING') } | ForEach-Object {
    $saved[$_.Name] = $_.Value
}
$results = @()
try {
    Get-ChildItem Env: | Where-Object Name -like 'CCL_*' | ForEach-Object { Remove-Item -LiteralPath ('Env:' + $_.Name) }
    $env:TEMP = $scratch
    $env:TMP = $scratch
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:PYTHONUNBUFFERED = '1'                        # retain progress if a test times out or is interrupted
    $env:PYTHONIOENCODING = 'utf-8'                    # drive.py must not replace the unbuffered stream
    $env:CCL_NO_DOWNLOAD = '1'
    $env:CCL_NOOPEN = '1'
    $env:CCL_QUIET = '1'
    $env:CCL_ROSTER_LINK = Join-Path $scratch 'roster-link.json'
    foreach ($t in $Tests) {
        $problems = @()
        $exitCode = $null
        $summary = ''
        $p = $null
        $job = [IntPtr]::Zero
        $timedOut = $false
        $log = Join-Path $logs "$t.log"
        $err = Join-Path $logs "$t.err"
        try {
            if ($t -notmatch '^[A-Za-z0-9_]+$' -or -not (Test-Path -LiteralPath (Join-Path $here "$t.py") -PathType Leaf)) {
                throw "Unknown test name: $t"
            }
            $job = [CCLTestJob]::Create()
            $p = Start-Process -FilePath $Python -ArgumentList "-B $t.py" -WorkingDirectory $here -RedirectStandardOutput $log -RedirectStandardError $err -WindowStyle Hidden -PassThru
            $processHandle = $p.Handle                 # retain access to ExitCode after Windows reaps the process
            [CCLTestJob]::Assign($job, $processHandle)
            if (-not $p.WaitForExit($TimeoutSeconds * 1000)) {
                $timedOut = $true
                $problems += "timed out after $TimeoutSeconds seconds"
                # Stop only this test's process tree, including mutation-test children.
                [CCLTestJob]::Stop($job)
                if (-not $p.WaitForExit(10000)) { throw 'Could not stop the timed-out test' }
            }
            $p.WaitForExit()                            # flush redirected output before reading it
            $p.Refresh()
            $exitCode = $p.ExitCode
            if ($null -eq $exitCode -or $exitCode -ne 0) { $problems += "exit code $exitCode" }
            $lines = @(Get-Content -LiteralPath $log)
            $summaries = @($lines | Where-Object { $_ -match '^\s*\d+ of \d+ checks passed\s*$' })
            if ($summaries.Count -eq 0) {
                $problems += 'missing final assertion summary'
            } else {
                $summary = $summaries[-1].Trim()
                if ($summary -match '^(\d+) of (\d+) checks passed$') {
                    if ([int]$Matches[2] -eq 0 -or [int]$Matches[1] -ne [int]$Matches[2]) { $problems += 'assertions failed or no checks ran' }
                }
            }
            if (@($lines | Where-Object { $_ -match '^\s*FAIL(?:\s|$)' }).Count -gt 0) { $problems += 'FAIL assertion in output' }
            # Do not hide Tk callback errors (or any other stderr) behind a passing count.
            if ((Get-Item -LiteralPath $err).Length -gt 0) { $problems += 'stderr is not empty (see retained log)' }
        } catch {
            $problems += $_.Exception.Message
        } finally {
            [CCLTestJob]::Close($job)                    # no subprocess outlives its test, even on an error
            if ($null -ne $p) {
                if (-not $p.HasExited) { $p.Kill(); $p.WaitForExit(10000) | Out-Null }
                $p.Dispose()
            }
        }
        $passed = $problems.Count -eq 0
        $result = [pscustomobject]@{ Test = $t; Passed = $passed; ExitCode = $exitCode; TimedOut = $timedOut; Summary = $summary; Problems = $problems; Stdout = $log; Stderr = $err }
        $results += $result
        if ($passed) { "${t}: PASS - $summary" } else { "${t}: FAIL - " + ($problems -join '; ') }
    }
} finally {
    Get-ChildItem Env: | Where-Object { $_.Name -like 'CCL_*' -or $_.Name -in @('TEMP', 'TMP', 'PYTHONDONTWRITEBYTECODE', 'PYTHONUNBUFFERED', 'PYTHONIOENCODING') } | ForEach-Object {
        Remove-Item -LiteralPath ('Env:' + $_.Name)
    }
    foreach ($name in $saved.Keys) { Set-Item -LiteralPath ('Env:' + $name) -Value $saved[$name] }
    ConvertTo-Json -InputObject @($results) -Depth 4 | Set-Content -LiteralPath (Join-Path $run 'results.json') -Encoding UTF8
}
$failed = @($results | Where-Object { -not $_.Passed }).Count
"$($results.Count - $failed) of $($results.Count) test sets passed. Logs: $logs"
if ($failed -gt 0 -or $results.Count -eq 0) { exit 1 }
exit 0
