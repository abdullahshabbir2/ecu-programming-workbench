from pathlib import Path
import os
import subprocess
import sys
os.chdir(Path(__file__).parent)
Path('evidence').mkdir(exist_ok=True)
result=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-v'],capture_output=True,text=True)
Path('evidence/tests.txt').write_text(result.stdout+result.stderr,encoding='utf-8')
print(result.stdout+result.stderr)
if result.returncode: raise SystemExit(result.returncode)
subprocess.run([sys.executable,'-m','workbench','demo','--output','evidence'],check=True)
