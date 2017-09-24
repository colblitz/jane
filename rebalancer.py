import hashlib
import hmac
import requests
import time
import sqlite3
import threading
import os
import uuid
import sys
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
DROP TABLE IF EXISTS trade_log;
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
	subtotal REAL,
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

db = getDb(config.DB_FILE_LOG)

def query_db(query, args=(), one=False):
	cur = db.execute(query, args)
	rv = cur.fetchall()
	cur.close()
	return (rv[0] if rv else None) if one else rv

def getPortfolioValue():
	return query_db("SELECT sum(amount_usd) AS amount FROM transfer_log")[0][0]

def insertManualValue(amt_usd, amt_btc):
	db.cursor().execute(
		"INSERT INTO transfer_log(timestamp, type, amount_usd, amount_btc) VALUES (?,?,?,?)",
		(int(time.time()), "MANUAL", float(amt_usd), float(amt_btc)))
	db.commit()

def insertProfitValue(amt_usd, amt_btc):
	db.cursor().execute(
		"INSERT INTO transfer_log(timestamp, type, amount_usd, amount_btc) VALUES (?,?,?,?)",
		(int(time.time()), "PROFIT", float(amt_usd), float(amt_btc)))
	db.commit()

def insertTransfer(type, amt_usd, amt_btc, fee_usd, fee_btc):
	db.cursor().execute(
		"INSERT INTO transfer_log(timestamp, type, amount_usd, amount_btc, fee_usd, fee_btc) VALUES (?,?,?,?,?,?)",
		(int(time.time()), type, float(amt_usd), float(amt_btc), float(fee_usd), float(fee_btc)))
	db.commit()

def insertTrade(exchange, ttype, quantity, price, fee):
	st = float(quantity) * price
	db.cursor().execute(
		"INSERT INTO trade_log(timestamp, exchange, type, quantity, price, subtotal, fee, total) VALUES (?,?,?,?,?,?,?,?)",
		(int(time.time()), exchange, ttype, float(quantity), float(price), st, float(fee), st + fee))
	db.commit()

############################
####  COINBASE METHODS  ####
############################

client = Client(
	config.COINBASE_API_KEY,
	config.COINBASE_API_SECRET,
	api_version='2017-04-13'
)

# {
#   "balance": {
#     "amount": "0.22940912",
#     "currency": "BTC"
#   },
#   "created_at": "2017-06-05T22:17:58Z",
#   "currency": "BTC",
#   "id": "ef5f8ef6-58ba-5a75-84c2-54765c1bac3d",
#   "name": "BTC Wallet",
#   "native_balance": {
#     "amount": "812.60",
#     "currency": "USD"
#   },
#   "primary": true,
#   "resource": "account",
#   "resource_path": "/v2/accounts/ef5f8ef6-58ba-5a75-84c2-54765c1bac3d",
#   "type": "wallet",
#   "updated_at": "2017-09-18T16:27:35Z"
# }

BTC_WALLET_NAME = "BTC Wallet"
USD_WALLET_NAME = "USD Wallet"

class CoinbaseAccount:
	def __init__(self, client):
		self.client = client
		self.accounts = client.get_accounts()
		self.paymentMethods = client.get_payment_methods()

		for account in self.accounts['data']:
			if account['name'] == BTC_WALLET_NAME:
				self.accountBTC = account
				self.accountIdBTC = account['id']
			if account['name'] == USD_WALLET_NAME:
				self.accountUSD = account
				self.accountIdUSD = account['id']

		for pm in self.paymentMethods['data']:
			if pm['name'] == USD_WALLET_NAME:
				self.paymentMethodUSD = pm
				self.paymentMethodIdUSD = pm['id']

	def getBTCBalanceInUSD(self):
		return self.accountBTC["native_balance"]["amount"]

	def sellBTC(self, amount):
		# TODO
		self.client.sell(
			self.id,
			total = str(amount),
			currency = 'BTC',
			payment_method = self.paymentMethodIdUSD)

	def sendBTC(self, address, amount):
		if amount > self.accountBTC["balance"]["amount"]:
			return
		return self.client.send_money(
			self.id,
			to = address,
			amount = str(amount),
			currency = 'BTC',
			idem = uuid.uuid1())

	def buyBTC(self):
		pass

	def withdrawUSD(self):
		pass

def getAccount():
	return CoinbaseAccount(client)

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

def placeOrder(ttype, url, market, quantity, rate, timeout):
	r = makeBittrexRequest(
		url,
		"market={}&quantity={:.8f}&rate={:.8f}",
		[market, quantity, rate])
	uuid = r['uuid']
	log("Created order with uuid: " + uuid)
	time.sleep(timeout)
	orderDetails = getOrderDetails(uuid)
	if orderDetails['IsOpen']:
		cancelOrder(uuid)
		time.sleep(5)
		return False
	else:
		insertTrade(market, ttype, quantity, rate, orderDetails['CommissionPaid'])
		return True

def placeLimitSell(market, quantity, rate, timeout):
	return placeOrder("LIMIT SELL", "https://bittrex.com/api/v1.1/market/selllimit", market, quantity, rate, timeout)

def placeLimitBuy(market, quantity, rate, timeout):
	return placeOrder("LIMIT BUY", "https://bittrex.com/api/v1.1/market/buylimit", market, quantity, rate, timeout)

def getDepositAddress(currency):
	return makeBittrexRequest(
		"https://bittrex.com/api/v1.1/account/getdepositaddress",
		"currency={}",
		[currency])["Address"]

def withdraw(currency, amount, address):
	return makeBittrexRequest(
		"https://bittrex.com/api/v1.1/account/withdraw",
		"currency={}&quantity={}&address={}",
		[currency, amount, address])["uuid"]

########################
####  OTHER THINGS  ####
########################

def getCoinValuesUSD():
	response = requests.get(TICKER_URL % TICKER_LIMIT)
	values = {}
	allDetails = {}
	for details in response.json():
		values[details["symbol"]] = Decimal(str(details["price_usd"]))
		allDetails[details['symbol']] = details
	return values, allDetails

def normalize(m):
	normalized = {}
	total = float(sum(m.values()))
	for k in m:
		normalized[k] = float(m[k]) / total
	return normalized

# Return map of symbol to value percentage
def getAllocation(coinDetails, balance):
	# equal distribution of top 20
	allocation = {}

	try:
		cat = config.CUSTOM_ALLOCATION_TIERS
		# sortedCoins = sorted(coinDetails.items(), key = lambda x: int(x[1]['rank']))
		for c in coinDetails:
			r = int(coinDetails[c]['rank'])
			for i, t in enumerate(cat):
				if r <= t[0]:
					allocation[c] = float(t[1]) / (t[0] if i == 0 else t[0] - cat[i-1][0])
					break
	except Exception:
		pass

	try:
		cas = config.CUSTOM_ALLOCATION_SPECIFIC
		for l in cas:
			for c in l[0]:
				allocation[c] = l[1] / len(l[0])
	except Exception:
		pass

	try:
		ca = config.CUSTOM_ALLOCATION
		for c in coinDetails:
			r = int(coinDetails[c]['rank'])
			if 'major' in ca and r <= ca['major'][0]:
				allocation[c] = float(ca['major'][1]) / ca['major'][0]
			elif 'minor' in ca and 'major' in ca and r <= ca['minor'][0] and r > ca['major'][0]:
				allocation[c] = float(ca['minor'][1]) / (ca['minor'][0] - ca['major'][0])
			elif 'specific' in ca and c in ca['specific'][0]:
				allocation[c] = float(ca['specific'][1]) / len(ca['specific'][0])
	except Exception:
		pass

	if len(allocation) == 0:
		allCoins = set(balance.keys())
		print allCoins
		for coin in allCoins:
			allocation[coin] = 1.0 / len(allCoins)
	return normalize(allocation)

def getTargetAmounts(coinValuesUSD, allocation, pv):
	targets = {}
	for coin in allocation:
		coinPriceUSD = coinValuesUSD[coin]
		targetValueUSD = Decimal(allocation[coin]) * pv
		targetCoinAmount = targetValueUSD / coinPriceUSD
		targets[coin] = targetCoinAmount
	return targets

def tryToMakeOrder(coin, amount):
	marketValues = getMarket("BTC-"+coin)
	if marketValues:
		ask = marketValues['Ask']
		bid = marketValues['Bid']
		step = (ask - bid) / float(config.ORDER_RETRIES)

		tryRate = ask if amount > 0 else bid
		i = 0
		success = False
		while i < config.ORDER_RETRIES:
			log("# Try {} to order {} for coin at {}".format(i, coin, tryRate))
			# TODO - re-look up market?
			if amount < 0 and placeLimitSell("BTC-" + coin, abs(amount), tryRate, config.ORDER_TIMEOUT):
				success = True
				break
			elif amount > 0 and placeLimitBuy("BTC-" + coin, amount, tryRate, config.ORDER_TIMEOUT):
				success = True
				break
			tryRate -= (step if amount < 0 else (-1 * step))
			i += 1
		log("Order successful: {}".format(success))
	else:
		log("No market for {}".format(coin))

def makeOrders(coinValuesUSD, balance, targets):
	for c in targets:
		log("-----")
		t = Decimal(targets[c])
		h = Decimal(balance.get(c, 0))
		d = Decimal(t - h)
		v = abs(d) * coinValuesUSD[c]
		if c == "BTC" or c == "BCH":
			# TODO
			log("Skipping BTC/BCH for now")
			continue
		else:
			if v > config.REBALANCE_THRESHOLD_VALUE and abs(d) > h * Decimal(config.REBALANCE_THRESHOLD_RATIO):
				log("Try to make order for {} of {}".format(c, d))
				tryToMakeOrder(c, d)
			else:
				log("Too small of an order for {}, skipping ({} | {})".format(c, v, (abs(d) / h) if h > 0 else 0))

tab = "   "

def logBalance(balance):
	if balance:
		log("Balance:")
		for c in balance:
			log(tab + c + ": " + str(balance[c]))
	else:
		log("##### No balance found")

def logAllocation(allocation, balance, targets):
	log("Allocations:")
	sortedAllocation = sorted(allocation.items(), key=lambda x: x[1], reverse=True)
	log("{}{:<5} | {:<15} | {:<15} | {:<15} | {:<15}".format(
		tab,
		"coin",
		"alloc",
		"have",
		"target",
		"diff"))
	for c in sortedAllocation:
		log("{}{:>5} | {:>15.8f} | {:>15.8f} | {:>15.8f} | {:> 15.8f}".format(
			tab,
			c[0],
			c[1],
			balance.get(c[0], 0),
			targets[c[0]],
			targets[c[0]] - balance.get(c[0], 0)))
		# log(tab + c[0] + ": " + str(c[1]))


def logPortfolio(av, sv):
	log("Portfolio Value:")
	log(tab + "Supposed: " + str(sv))
	log(tab + "  Actual: " + str(av))

if __name__ == "__main__":
	coinValuesUSD, allDetails = getCoinValuesUSD()

	# TODO: check if coinbase has btc, if so, transfer in
	coinbase = getAccount()
	# print coinbase.client.get_accounts()
	# print coinbase.client.get_payment_methods()
	# print coinbase.walletIdUSD

	print getDepositAddress("BTC")

	balance = getBalance()
	logBalance(balance)

	actualValue = Decimal(0.0)
	for c in balance:
		actualValue += balance[c] * coinValuesUSD[c]

	supposedValue = Decimal(getPortfolioValue())
	moveAmount = 0
	if actualValue - supposedValue > config.TRANSFER_THRESHOLD:
		profit = actualValue - supposedValue
		log("Profit: {}".format(profit))
		moveAmount = profit * (1 - config.PROFIT_RATIO_TO_KEEP)
		keepAmount = profit * config.PROFIT_RATIO_TO_KEEP
		supposedValue += keepAmount
		insertProfitValue(keepAmount, keepAmount / coinValuesUSD['BTC'])
	elif actualValue - supposedValue < -1 * config.TRANSFER_THRESHOLD:
		# TODO: buy more or do nothing
		pass

	logPortfolio(actualValue, supposedValue)

	# insertManualValue(4000, 4000 / coinValuesUSD['BTC'])

	allocation = getAllocation(allDetails, balance)
	targets = getTargetAmounts(coinValuesUSD, allocation, supposedValue)
	logAllocation(allocation, balance, targets)

	makeOrders(coinValuesUSD, balance, targets)

	# TODO: send profits out
	if moveAmount > 0:
		# transfer to coinbase
		# transfer to bank
		pass

	# Check speculation







