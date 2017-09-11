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

# nonce = int(time.time());
# url = "https://bittrex.com/api/v1.1/account/getbalances?apikey=%s&nonce=%d" % (config.READONLY_API_KEY, nonce)
# print url

# sign = hmac.new(config.READONLY_API_SECRET, url, hashlib.sha512);
# r = requests.get(url, headers={'apisign': sign.hexdigest()})
# # pprint(r.json())
# if not r.json()["success"]:
# 	print r.json()["message"]

# balances = {}
# for details in r.json()["result"]:
# 	available = details["Available"]
# 	balance = details["Balance"]
# 	address = details["CryptoAddress"]
# 	currency = details["Currency"]
# 	pending = details["Pending"]
# 	print balance, available, pending, currency, address
# 	balances[currency] = balance

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
db = None
cwd = os.path.dirname(os.path.abspath(__file__))
INIT_SCRIPT = """
DROP TABLE IF EXISTS test;
DROP TABLE IF EXISTS requests;
CREATE TABLE test (
	c integer
);
CREATE TABLE requests (
	id INTEGER PRIMARY KEY,
	timestamp INTEGER,
	response TEXT
);
"""

class TickerThread(threading.Thread):
	i = 0
	# db

	def initDb(self):
		global db
		if not db:
			db = sqlite3.connect(cwd + "/" + config.DB_FILE_TICKER_RAW)
			db.row_factory = sqlite3.Row
			db.cursor().executescript(INIT_SCRIPT)
			print "init db"

	def getTicker(self):
		global db
		timestamp = int(time.time())
		response = requests.get("https://api.coinmarketcap.com/v1/ticker?limit=3")
		db.cursor().execute("INSERT INTO requests(timestamp, response) VALUES (?, ?)", (timestamp, response.content))
		db.commit()

	def run(self):
		global db
		self.initDb()
		while self.i < 3:
			print self.i
			db.cursor().execute("INSERT INTO test(c) VALUES (?)", (self.i,))
			db.commit()
			self.i += 1
			self.getTicker()
			print "sleeping for 10"
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