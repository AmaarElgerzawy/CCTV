with open('test_static_checks.py') as f:
    for i,l in enumerate(f,1):
        if i>40: break
        print(i,l)
