import sys, os, re, json, traceback
sys.path.insert(0, r"c:\appdev\kiosk_projekt")
os.chdir(r"c:\appdev\kiosk_projekt")

try:
    from app import app as flask_app
except Exception as e:
    print('ERROR importing app:', e)
    traceback.print_exc()
    raise

client = flask_app.test_client()
try:
    resp = client.get('/')
    print('STATUS:', resp.status_code)
    text = resp.get_data(as_text=True)
    print('LENGTH:', len(text))
    print('\n--- HTML START ---\n')
    print(text[:3000])
    print('\n--- HTML END (truncated) ---\n')

    m = re.search(r"<script[^>]+id=\"members-data\"[^>]*>(.*?)</script>", text, re.S)
    if m:
        js = m.group(1).strip()
        print('RAW members-data starts with:', js[:400])
        try:
            data = json.loads(js)
            print('PARSED members count:', len(data))
            print('SAMPLE:', data[:10])
        except Exception as e:
            print('JSON parse error:', e)
            traceback.print_exc()
    else:
        print('members-data script tag not found')
except Exception as e:
    print('Error during request:', e)
    traceback.print_exc()
