import os
for f in os.listdir('.'):
    if f.endswith('.py'):
        try:
            s=open(f).read()
            if 'cgi_sync_patch' in s:
                print(f)
        except Exception:
            pass
