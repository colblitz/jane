import hashlib
import hmac
import requests
import time
import sqlite3
import threading
import os
from pprint import pprint

import config

# prices = {}
# for details in requests.get("https://api.coinmarketcap.com/v1/ticker").json():
# 	prices[details["symbol"]] = float(details["price_usd"])
# 	# "24h_volume_usd"
# 	# "available_supply"
# 	# "id"
# 	# "last_updated"
# 	# "market_cap_usd"
# 	# "name"
# 	# "percent_change_1h"
# 	# "percent_change_24h"
# 	# "percent_change_7d"
# 	# "price_btc"
# 	# "price_usd"
# 	# "rank"
# 	# "symbol"
# 	# "total_supply"

def log(s):
	if True:
		print "[%d] %s" % (int(time.time()), s)

def getBalance():
	nonce = int(time.time());
	url = "https://bittrex.com/api/v1.1/account/getbalances?apikey=%s&nonce=%d" % (config.READONLY_API_KEY, nonce)
	log(url)

	sign = hmac.new(config.READONLY_API_SECRET, url, hashlib.sha512);
	r = requests.get(url, headers={'apisign': sign.hexdigest()})
	if not r.json()["success"]:
		log(r.json()["message"])
		return None

	balances = {}
	for details in r.json()["result"]:
		available = details["Available"]
		balance = details["Balance"]
		address = details["CryptoAddress"]
		currency = details["Currency"]
		pending = details["Pending"]
		print balance, available, pending, currency, address
		balances[currency] = balance

# print ""
# print "in usd:"

# total = 0
# for b in balances:
# 	if balances[b] > 0:
# 		total += prices[b] * balances[b]
# 		print b, prices[b] * balances[b]
# print "TOTAL: ", total
	# print type(b)
	# print type(prices[b])
	# print type(balances[b])

# DATABASE = config.DB_FILE_TICKER_RAW

# import sqlite3
# conn = sqlite3.connect('Database/testDB.db')
INIT_SCRIPT = """
DROP TABLE IF EXISTS requests;
CREATE TABLE requests (
	id INTEGER PRIMARY KEY,
	timestamp INTEGER,
	response TEXT
);

"""

def getDb(dbFile):
	existed = False
	if os.path.isfile(dbFile):
		existed = True

	cwd = os.path.dirname(os.path.abspath(__file__))
	db = sqlite3.connect(cwd + "/" + dbFile)

	if not existed:
		print "db created, running init script"
		db.cursor().executescript(INIT_SCRIPT)
	return db

TICKER_URL = "https://api.coinmarketcap.com/v1/ticker?limit=%d"
TICKER_LIMIT = 10
TICKER_INTERVAL = 5

def getTicker(n):
	timestamp = int(time.time())
	response = requests.get(TICKER_URL % n)
	return timestamp, response

def parseTicker(response):
	response.content

def saveRawTickerResponse(db, timestamp, response):
	db.cursor().execute("INSERT INTO requests(timestamp, response) VALUES (?, ?)", (timestamp, response.content))
	db.commit()

class TickerThread(threading.Thread):
	def run(self):
		rawDB = getDb(config.DB_FILE_TICKER_RAW)
		parsedDB = getDb(config.DB_FILE_TICKER_FORMATTED)

		i = 0
		while i < 3:
			timestamp, response = getTicker(TICKER_LIMIT)
			saveRawTickerResponse(rawDB, timestamp, response)
			i += 1
			print "sleeping"
			time.sleep(5)
		print "done"

if __name__ == '__main__':
	print "in main"
	tickerThread = TickerThread(name="TickerThread")
	tickerThread.daemon = True
	tickerThread.start()

	print "started t"
	tickerThread.join()
	print "joined t"