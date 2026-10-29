import glob,os
for f in glob.glob('test_*.py'):
    if 'direct' in f or f in ['test_run_b_new.py']:
        try:
            os.remove(f)
            print('rem',f)
        except Exception as e:
            print(e)
print('done')
