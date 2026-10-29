with open('registry.py') as f:
    for i,l in enumerate(f,1):
        if 'snapshot' in l or 'run_once' in l or 'async def' in l:
            if 'StatusChecker' in l or 'snapshot' in l or 'run_once' in l:
                print(i,l.rstrip())