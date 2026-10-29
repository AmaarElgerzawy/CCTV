import subprocess
r = subprocess.run(['py','-W','error::RuntimeWarning','-m','unittest','test_phase6a_health_required.Phase6aHealthRequiredTests.test_c_second_check_all_conflict'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
print(r.stdout.decode()[-200:])
print(r.stderr.decode()[-300:])
print(r.returncode)
