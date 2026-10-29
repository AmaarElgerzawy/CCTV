with open('health_checker.py') as f:
    content = f.read()
print('httpx' in content)
for i,l in enumerate(content.splitlines()):
    if 'AsyncClient' in l or 'post' in l or 'get' in l:
        print(i+1, l)
        if i > 60: break
