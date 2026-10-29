import subprocess,glob
r=subprocess.run(['py','-m','pyflakes']+glob.glob('*.py'),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
print('PYFLAKES_OUT:', r.stdout.decode())
print('PYFLAKES_ERR:', r.stderr.decode())
r2=subprocess.run(['py','-W','error::RuntimeWarning','-m','unittest','discover','-p','test_*.py'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
out=r2.stdout.decode()+r2.stderr.decode()
print(out[-300:])
