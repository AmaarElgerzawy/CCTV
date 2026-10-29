import subprocess
r = subprocess.run(['py','-W','error::RuntimeWarning','-m','unittest','discover','-p','test_*.py'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
out = r.stdout.decode() + r.stderr.decode()
print(out[-200:])
