import glob
for t in sorted(glob.glob('test*.py')):
    with open(t) as f:
        content = f.read()
    if 'health' in content or 'check-all' in content or '/api/health' in content:
        print('FOUND:', t)
        # print first occurrence
        for i,l in enumerate(content.splitlines()):
            if 'health' in l or 'check-all' in l or '/api/health' in l:
                print(f'  {i+1}: {l}')
                break
    else:
        pass
print('done')
