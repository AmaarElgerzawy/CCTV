import glob,os
for f in glob.glob('test_*.py'):
    with open(f) as fh:
        try:
            c=fh.read()
        except Exception:
            continue
    if 'test_phase6a_health_fix5' in c:
        print('BAD',f)
        try:
            os.remove(f)
            print('removed')
        except Exception as e:
            print(e)
print('done')
