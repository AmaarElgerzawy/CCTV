import os
files = ['app_patch_helper.py','cgi_patch.py','check_cgi.py','replace_logs.py','debug_404.py','debug_404b.py','debug_404c.py','debug_app.py','debug_appstate.py','debug_cache2.py','debug_cache4.py','debug_cache5.py','debug_cache6.py','debug_cache7.py','debug_compare.py','debug_compare2.py','debug_compare3.py','debug_direct.py','debug_fix.py','debug_fix2.py','debug_fix3.py','debug_inside.py','debug_mystery.py','debug_mystery2.py','debug_mystery3.py','debug_patch_like_old.py','debug_patch_manual.py','debug_patch_manual2.py','debug_proper.py','debug_real.py','debug_test.py','debug_trace_route.py','debug_weird.py','debug_what.py','debug_what2.py','count.py','count_clock.py','dump_find.py','dump_find2.py','dump_time.py','find_rec.py','inspect_cgi.py','read_app.py','read_app2.py','read_app3.py','search_patch.py','show_test.py','test_patch.py','trace.py','trace_rec.py','trace_rec2.py','check_pyflakes.py','debug_appclient.py','debug_appclient2.py','debug_appclient3.py','debug_appclient4.py','debug_clock.py','debug_clock2.py','debug_deep.py','check_routes.py','check_routes2.py']
for f in files:
    try:
        if os.path.exists(f):
            os.remove(f)
            print('removed', f)
    except Exception as e:
        print('err', f, e)
