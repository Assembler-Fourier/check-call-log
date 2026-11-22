"""Optional behavioral checks for run_all.ps1, using disposable synthetic tests.

    python -B t_runner.py

Run directly to avoid excessively nested scratch paths in Windows PowerShell 5.1.
No application window, production data or live service is used. Each subprocess
gets a temporary execution-policy override; no Windows policy is changed.
"""
import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

HERE = Path(__file__).resolve().parent
WORK = Path(tempfile.gettempdir()) / "ccl-tests" / ("runner-" + uuid.uuid4().hex)
fixtures = WORK / "fixtures"
fixtures.mkdir(parents=True)
shutil.copyfile(HERE / "run_all.ps1", fixtures / "run_all.ps1")
cases = {
    "pass": ("print('2 of 2 checks passed')", True),
    "exit": ("print('2 of 2 checks passed'); raise SystemExit(7)", False),
    "count": ("print('1 of 2 checks passed')", False),
    "fail": ("print('FAIL  hidden regression'); print('2 of 2 checks passed')", False),
    "missing": ("print('started')", False),
    "stderr": ("import sys; print('error', file=sys.stderr); print('2 of 2 checks passed')", False),
    "timeout": ("import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)']); "
                "print('CHILD '+str(p.pid),flush=True); time.sleep(20); print('1 of 1 checks passed')", False),
    "environment": ("import os,tempfile; assert not any(k in os.environ for k in ('CCL_WA','CCL_WATCH','CCL_SCRIPT_CHECK')); "
                    "assert os.environ['CCL_NO_DOWNLOAD']=='1'; "
                    "assert os.environ['TEMP']==os.environ['TMP']==tempfile.gettempdir(); print('1 of 1 checks passed')", True),
}
for name, (body, _) in cases.items():
    (fixtures / ("t_" + name + ".py")).write_text(body + "\n", encoding="utf-8")
cases["nonexistent"] = ("", False)
passed = 0
for name, (_, expected) in cases.items():
    env = dict(os.environ, CCL_WA="1", CCL_WATCH="1", CCL_SCRIPT_CHECK="1")
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(fixtures / "run_all.ps1"),
         "-Tests", "t_" + name, "-WorkRoot", str(WORK / name),
         "-TimeoutSeconds", "1" if name == "timeout" else "10", "-Python", sys.executable],
        capture_output=True, text=True, env=env, creationflags=subprocess.CREATE_NO_WINDOW, timeout=30,
    )
    records = list((WORK / name).glob("*/results.json"))
    if len(records) != 1:
        print(proc.stdout, proc.stderr)
        raise SystemExit("runner did not preserve one results file for " + name)
    result = json.loads(records[0].read_text(encoding="utf-8-sig"))[0]
    ok = (proc.returncode == 0) == expected and result["Passed"] == expected
    if name == "timeout":
        ok = ok and result["TimedOut"] and not any("Could not stop" in s for s in result["Problems"])
        output = Path(result["Stdout"]).read_text()
        child_pid = int(next(s.split()[1] for s in output.splitlines() if s.startswith("CHILD ")))
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x100000, 0, child_pid)  # only the child this check created
        if handle:
            ok = ok and kernel.WaitForSingleObject(handle, 0) == 0
            kernel.CloseHandle(handle)
        else:
            ok = ok and ctypes.get_last_error() == 87    # PID no longer exists
    if name == "exit":
        ok = ok and result["ExitCode"] == 7
    passed += ok
    print(("PASS" if ok else "FAIL") + " " + name, result["Problems"])
    if not ok:
        print(proc.stdout, proc.stderr)
print("%d of %d checks passed" % (passed, len(cases)))
raise SystemExit(0 if passed == len(cases) else 1)
