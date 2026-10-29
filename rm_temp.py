import os
files = [
'add_find_last.py','append_app.py','append_health.py','bak.py','check_flake.py','check_flake2.py','clean_bad.py',
'find_update.py','fix_app_put.py','fix_app_sched.py','fix_cgi_import.py','fix_devices_form2.py','fix_render.py',
'fix_round.py','fix_status.py','flake_ok.py','git_stat.py','grep_dt.py','grep_fields.py','grep_mig.py',
'grep_pub.py','grep_put.py','grep_sched.py','grep_schema.py','grep_time.py','listall.py','ls.py',
'read_app.py','read_cgi.py','read_cgi_pages.py','read_env.py','read_more.py','read_pub.py','read_put.py',
'read_registry.py','read_registry2.py','read_schema.py','read_test_registry.py','read_update.py',
'run_tests.py','search_cgi.py','show_end_env.py','show_mig.py','tail_app.py','tail_readme.py',
'tail_reg.py','update_devices_js.py','update_tail.py'
]
for f in files:
    try:
        if os.path.exists(f):
            os.remove(f)
            print('r',f)
    except Exception as e:
        print('e',f,e)
