import hashlib
import hmac
import requests
import time
from pprint import pprint

import config

nonce = int(time.time());
url = "https://bittrex.com/api/v1.1/account/getbalances?apikey=%s&nonce=%d" % (config.API_KEY, nonce)
print url

sign = hmac.new(config.API_SECRET, url, hashlib.sha512);
r = requests.get(url, headers={'apisign': sign.hexdigest()})
pprint(r.json())

pprint(requests.get("https://api.coinmarketcap.com/v1/ticker/?limit=10").json())