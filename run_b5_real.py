import subprocess
r = subprocess.run(['py','-W','error::RuntimeWarning','-m','unittest','test_phase6a_health_fix5.HealthAllDevicesTests.test_b_check_all_with_mocks'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
print(r.stdout.decode()[-300:])
print(r.stderr.decode()[-400:])
print(r.returncode)
