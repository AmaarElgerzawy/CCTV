try:
    import cgi_client
    print('ok')
except Exception as e:
    print(e)
    import traceback
    traceback.print_exc()
