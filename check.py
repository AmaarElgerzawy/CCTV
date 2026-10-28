import subprocess,glob
r=subprocess.run(['py','-m','pyflakes']+glob.glob('*.py'),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
print(r.stdout.decode())
print(r.stderr.decode())
