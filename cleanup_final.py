import os
files = ['add_gitignore.py','analyze_files.py','append_readme.py','check_clean.py','check_imports2.py','check_left.py','count.ps1','del_scratch.py','find_imports.py','git_status.py','listdir.py','make_scratch.py','read_gitignore.py']
for f in files:
    try:
        if os.path.exists(f):
            os.remove(f)
            print('removed',f)
    except Exception as e:
        print('err',f,e)
