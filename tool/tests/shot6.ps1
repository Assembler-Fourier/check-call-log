# Open the real window once on screen, picture it, close it. Safe by construction:
#  - the window closes itself after $Life seconds whatever happens here (CCL_QUIT_AFTER);
#  - the picture is taken in a separate job with a time limit, so a stuck capture cannot hold the window open;
#  - only the process started here is ever pictured or closed; the user's settings and corrections are not written
#    (CCL_THEME stops settings being saved, CCL_EDITS and CCL_OUT point at scratch folders).
param([string]$Who = 'lena', [string]$Day = '03/10/2026', [string]$Tag = 'a', [string]$Tab = 'calls', [string]$Theme = 'light',
      [string]$Geometry = '1240x960+40+10', [string]$App = 'app.pyw', [string]$Now = '', [int]$Settle = 900, [string]$Edits = 'edits-show', [int]$Life = 30, [uint32]$Flags = 2)
$sp = Join-Path $env:TEMP "ccl-tests"; New-Item -ItemType Directory -Force $sp | Out-Null      # throwaway output (pictures, logs, scratch corrections)
$png = Join-Path $sp "win-$Tag.png"; $done = Join-Path $sp "done-$Tag.txt"
Remove-Item $png, $done -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force (Join-Path $sp "out-$Tag") | Out-Null
$env:CCL_AUTORUN = "$Who|$Day"; $env:CCL_NOOPEN = '1'; $env:CCL_OUT = Join-Path $sp "out-$Tag"; $env:CCL_DONE_FILE = $done
$env:CCL_GEOMETRY = $Geometry; $env:CCL_NO_DOWNLOAD = '1'; $env:CCL_AUTORUN_DELAY = '1200'; $env:CCL_TAB = $Tab
$env:CCL_THEME = $Theme; $env:CCL_EDITS = Join-Path $sp $Edits; $env:CCL_QUIT_AFTER = "$Life"; $env:CCL_QUIET = '1'; if ($Now) { $env:CCL_NOW = $Now }
$proc = Start-Process pythonw -ArgumentList "-B `"$(Split-Path -Parent $PSScriptRoot)\$App`"" -PassThru
Remove-Item Env:CCL_AUTORUN, Env:CCL_NOOPEN, Env:CCL_OUT, Env:CCL_DONE_FILE, Env:CCL_GEOMETRY, Env:CCL_NO_DOWNLOAD, Env:CCL_AUTORUN_DELAY, Env:CCL_TAB, Env:CCL_THEME, Env:CCL_EDITS, Env:CCL_QUIT_AFTER, Env:CCL_QUIET, Env:CCL_NOW -ErrorAction SilentlyContinue
for ($i = 0; $i -lt 40 -and -not (Test-Path $done); $i++) { Start-Sleep -Milliseconds 500 }
Start-Sleep -Milliseconds $Settle
$proc.Refresh()
"${Tag}: window pid $($proc.Id), logs made: $(Test-Path $done) after ~$([int]($i / 2))s"
$job = Start-Job -ArgumentList ([int64]$proc.MainWindowHandle), $png, $Flags -ScriptBlock {
    param($handle, $png, $flags)
    Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System; using System.Drawing; using System.Runtime.InteropServices;
public class Cap {
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  public static string Shot(IntPtr h, string path, uint flags) {
    RECT r; GetWindowRect(h, out r);
    using (var bmp = new Bitmap(r.R - r.L, r.B - r.T)) {
      bool ok; using (var g = Graphics.FromImage(bmp)) { IntPtr dc = g.GetHdc(); ok = PrintWindow(h, dc, flags); g.ReleaseHdc(dc); }
      bmp.Save(path, System.Drawing.Imaging.ImageFormat.Png);
      return (r.R - r.L) + "x" + (r.B - r.T) + " at " + r.L + "," + r.T + " ok=" + ok;
    }
  }
}
'@
    [void][Cap]::SetProcessDPIAware()
    [Cap]::Shot([IntPtr]$handle, $png, [uint32]$flags)
}
if (Wait-Job $job -Timeout 12) { "${Tag}: picture " + (Receive-Job $job) } else { "${Tag}: the picture did not come back within 12 s (flags $Flags)" }
Remove-Job $job -Force
if (-not $proc.HasExited) { [void]$proc.CloseMainWindow(); "${Tag}: closed when asked: $($proc.WaitForExit(15000))" } else { "${Tag}: the window had already closed itself" }
if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force; "${Tag}: had to be stopped" }
