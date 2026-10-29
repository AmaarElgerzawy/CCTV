with open('app.py') as f:
    for i,l in enumerate(f,1):
        if 'checker' in l or 'StatusChecker' in l or 'run_once' in l or 'snapshot' in l:
            print(i,l.rstrip())