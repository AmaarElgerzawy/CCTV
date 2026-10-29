import subprocess
r = subprocess.run(['py','-W','error::RuntimeWarning','-m','unittest','test_phase6a_health_fix2.HealthAllDevicesTests.test_a_three_devices_all_unknown'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
print(r.stdout.decode()[-150:])
print(r.stderr.decode()[-300:])
print(r.returncode)
