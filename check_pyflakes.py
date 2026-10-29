import subprocess,glob
for f in sorted(glob.glob('*.py')):
    r=subprocess.run(['py','-m','pyflakes',f],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if r.returncode!=0:
        print('ERR',f)
        print(r.stdout.decode())
        print(r.stderr.decode())
        raise SystemExit(1)
print('ok')
