with open('health_checker.py') as f:
    content = f.read()
print('httpx' in content)
print('DvrClient' in content)
