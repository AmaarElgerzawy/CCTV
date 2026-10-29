import os
for f in ['test_phase6a_health_fix.py','test_phase6a_health_fix2.py','test_phase6a_health_fix3.py','test_phase6a_health_fix4.py','test_phase6a_health_fix5.py']:
    try:
        os.remove(f)
    except Exception:
        pass
print('done')
