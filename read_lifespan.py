with open('app.py') as f:
    for i,l in enumerate(f,1):
        if 90 < i < 170:
            print(f'{i}: {l}', end='')