import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.update({
    "EPGENIUS_M3U_URL":"x",
    "EPGENIUS_EPG_URL":"x",
    "PROVIDER_URL":"https://real-provider.example",
    "PROVIDER_USERNAME":"REALUSER",
    "PROVIDER_PASSWORD":"REALPASS",
    "PUBLIC_BASE_URL":"http://192.168.1.50:38090",
})
import epgenius_rewriter as e
stats=e.rewrite_m3u(
    os.path.join(os.path.dirname(__file__),"epgenius.m3u"),
    os.path.join(os.path.dirname(__file__),"test-output.m3u")
)
print(stats)
with open(os.path.join(os.path.dirname(__file__),"test-output.m3u"),encoding="utf-8") as f:
    s=f.read()
print(s[:1800])
assert 'url-tvg="http://192.168.1.50:38090/epg.xml.gz"' in s
assert 'username":"REALUSER"' in s
assert 'password":"REALPASS"' in s
assert 'https://real-provider.example/live/REALUSER/REALPASS/' in s
print("TEST PASSED")
