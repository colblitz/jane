import hashlib
import hmac
import requests
import time
import sqlite3
import threading
import os
from decimal import *

from coinbase.wallet.client import Client

from pprint import pprint

import config

TICKER_URL = "https://api.coinmarketcap.com/v1/ticker?limit=%d"
TICKER_LIMIT = 200

def log(s):
	if True:
		print "[%d] %s" % (int(time.time()), s)

#####################
####  DB THINGS  ####
#####################

# transfer_log
#  - type is "Manual", "Coinbase", or "Profit"
#  - amounts are in BTC

# trade_log
#  - type is "Buy" or "Sell"
#  - total = quantity * price + fee

INIT_SCRIPT = """
DROP TABLE IF EXISTS transfer_log;
DROP TABLE IF EXISTS trading_log;
CREATE TABLE transfer_log (
	id INTEGER PRIMARY KEY,
	timestamp INTEGER,
	type TEXT,
	amount_usd REAL,
	amount_btc REAL,
	fee_usd REAL,
	fee_btc REAL
);
CREATE TABLE trade_log (
	id INTEGER PRIMARY KEY,
	timestamp INTEGER,
	exchange TEXT,
	type TEXT,
	quantity REAL,
	price REAL,
	fee REAL,
	total REAL
);
"""

def getDb(dbFile):
	existed = False
	cwd = os.path.dirname(os.path.abspath(__file__))
	absDb = cwd + "/" + dbFile
	if os.path.isfile(absDb):
		existed = True

	db = sqlite3.connect(absDb)

	if not existed:
		print "db created, running init script"
		db.cursor().executescript(INIT_SCRIPT)
	return db

def query_db(db, query, args=(), one=False):
	cur = db.execute(query, args)
	rv = cur.fetchall()
	cur.close()
	return (rv[0] if rv else None) if one else rv

def getPortfolioValue():
	db = getDb(config.DB_FILE_LOG)
	return query_db(db, "SELECT sum(amount_usd) AS amount FROM transfer_log")[0][0]

###########################
####  BITTREX METHODS  ####
###########################

def makeBittrexRequest(endpoint, urlargs, args):
	nonce = int(time.time());
	url = ("{}?apikey={}&nonce={}&" + urlargs).format(
		endpoint,
		config.BITTREX_API_KEY,
		nonce,
		*args)
	log("GET: " + url)

	sign = hmac.new(config.BITTREX_API_SECRET, url, hashlib.sha512)
	response = requests.get(url, headers={'apisign': sign.hexdigest()})
	if not response.json()["success"]:
		log(response.json()["message"])
		return None
	return response.json()['result']

# ex: getMarket("BTC-ARK")
# returns : {u'Ask': 0.00098, u'Bid': 0.00097114, u'Last': 0.00098}
def getMarket(exchange):
	return makeBittrexRequest(
		"https://bittrex.com/api/v1.1/public/getticker",
		"market={}",
		[exchange])

def getOrderDetails(uuid):
	return makeBittrexRequest(
		"https://bittrex.com/api/v1.1/account/getorder",
		"uuid={}",
		[uuid])

def getBalance():
	r = makeBittrexRequest(
		"https://bittrex.com/api/v1.1/account/getbalances",
		"",
		[])
	balances = {}
	for details in r if r else []:
		if details["Balance"] > 0:
			balances[details["Currency"]] = Decimal(str(details["Balance"]))
	return balances

def cancelOrder(uuid):
	return makeBittrexRequest(
		"https://bittrex.com/api/v1.1/market/cancel",
		"uuid={}",
		[uuid]) != None

# optional market string
def getOpenOrders():
	return makeBittrexRequest(
		"https://bittrex.com/api/v1.1/market/getopenorders",
		"",
		[])

def placeOrder(url, market, quantity, rate, timeout):
	r = makeBittrexRequest(
		url,
		"market={}&quantity={:.8f}&rate={:.8f}",
		[market, quantity, rate])
	uuid = r['uuid']
	log("Created order with uuid: " + uuid)
	time.sleep(timeout)
	if getOrderDetails(uuid)['IsOpen']:
		cancelOrder(uuid)
		return False
	else:
		# TODO: log to db
		return True

def placeLimitSell(market, quantity, rate, timeout):
	return placeOrder("https://bittrex.com/api/v1.1/market/selllimit", market, quantity, rate, timeout)

def placeLimitBuy(market, quantity, rate, timeout)
	return placeOrder("https://bittrex.com/api/v1.1/market/buylimit", market, quantity, rate, timeout)

########################
####  OTHER THINGS  ####
########################

def getCoinValuesUSD():
	response = requests.get(TICKER_URL % TICKER_LIMIT)
	values = {}
	for details in response.json():
		values[details["symbol"]] = Decimal(str(details["price_usd"]))
	return values

# Return map of symbol to value percentage
def getAllocation(coinValuesUSD, balance):
	# equal distribution of top 20
	allocation = {}
	allCoins = set(balance.keys())
	print allCoins
	for coin in allCoins:
		allocation[coin] = 1.0 / len(allCoins)
	return allocation

def getTargetAmounts(coinValuesUSD, allocation, pv):
	targets = {}
	for coin in allocation:
		coinPriceUSD = coinValuesUSD[coin]
		targetValueUSD = Decimal(allocation[coin] * pv)
		targetCoinAmount = targetValueUSD / coinPriceUSD
		targets[coin] = targetCoinAmount
	return targets

def rebalance():
	# TODO: transfer btc from coinbase

	balance = getBalance()
	if balance:
		for coin in balance:
			print coin, balance[coin]

	print ""

	coinValuesUSD = getCoinValuesUSD()
	allocation = getAllocation(coinValuesUSD, balance)
	print allocation
	print ""

	# get base amount
	pv = getPortfolioValue()
	print pv
	print ""

	targets = getTargetAmounts(coinValuesUSD, allocation, pv)
	for c in targets:
		print "%s: target %f, have %f" % (
			c,
			targets[c],
			balance.get(c, 0))

	print ""
	values = {}
	for coin in balance:
		coinAmount = balance[coin]
		coinValue = coinValuesUSD[coin] * coinAmount
		values[coin] = coinValue
	for c in values:
		print "%f %s at %f per = %f total" % (
			balance.get(c, 0),
			c,
			coinValuesUSD[c],
			values[c])
	return values

	# make orders
	# some sort of retry/step down of orders

	# send profits out


# rebalance()

# getOrderDetails("9c8b3048-e808-47a3-a2d3-9aebf6ce893d")