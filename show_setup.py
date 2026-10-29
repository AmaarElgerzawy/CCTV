with open('test_phase5b_regression.py') as f:
    lines = f.readlines()
for i,l in enumerate(lines[:100]):
    print(f'{i+1}: {l}', end='')
