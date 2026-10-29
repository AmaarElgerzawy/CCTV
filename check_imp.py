import os,re
bad=[]
for root,dirs,files in os.walk('.'):
    for fn in files:
        if fn.endswith('.py'):
            path=os.path.join(root,fn)
            try:
                s=open(path).read()
            except:
                continue
            for t in ['add_find_last','append_app','append_health','fix_app_put']:
                if re.search(r'^\s*(import|from)\s+'+re.escape(t)+r'\b',s,re.M):
                    bad.append((path,t))
print(bad)
