import glob, subprocess
tests = glob.glob('test_*.py')
print('testfiles:', len(tests))
r = subprocess.run(['py','-W','error::RuntimeWarning','-m','unittest','discover','-p','test_*.py'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
out = r.stdout.decode() + r.stderr.decode()
print(out[-200:])
