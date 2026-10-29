with open('dvr_client.py') as f:
    for i,l in enumerate(f):
        if 'AsyncClient' in l or 'httpx' in l:
            print(i+1, l.rstrip())
            if i > 80: break
    else:
        pass
