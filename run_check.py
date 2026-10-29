import subprocess,sys
subprocess.check_call([sys.executable,'-m','pyflakes','app.py','registry.py','health_checker.py','test_phase6a_health_required.py'])
subprocess.check_call([sys.executable,'-W','error::RuntimeWarning','-m','unittest','discover','-p','test_*.py'])
