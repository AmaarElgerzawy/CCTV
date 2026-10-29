with open('dvr_client.py') as f:
    for i,l in enumerate(f):
        if 'httpx' in l:
            print(i+1, l.rstrip())
            break
    else:
        print('none')
