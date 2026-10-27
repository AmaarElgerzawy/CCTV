s=open('app.py').read()
cnt=0
for line in s.splitlines():
    if 'Unexpected' in line and 'log_unexpected' not in line and 'logger.error' in line:
        print(line)
        cnt+=1
print('CNT', cnt)
