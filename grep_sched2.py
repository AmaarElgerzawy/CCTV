with open('app.py') as f:
    for i,l in enumerate(f,1):
        if '_HEALTH' in l or '_health_scheduler' in l or 'lifespan' in l:
            print(i,l.rstrip())