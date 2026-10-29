with open('test_phase5b_regression.py') as f:
    content = f.read()
for line in content.splitlines():
    if 'import' in line and ('app' in line):
        print(line)
        break
