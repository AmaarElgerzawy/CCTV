import subprocess,sys
r=subprocess.run([sys.executable,'-W','error::RuntimeWarning','-m','unittest','discover','-p','test_*.py'])
sys.exit(r.returncode)
