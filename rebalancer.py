import hashlib
import hmac
import requests
import time
import sqlite3
import threading
import os
import uuid
import sys
import argparse
import json
import base64
import re
import socket
import datetime
from decimal import *
from pprint import pprint
from requests.auth import AuthBase

import config

#######################
####  UTIL THINGS  ####
#######################

def getIP():
	return socket.gethostbyname(socket.gethostname())

def getLastDepositTime():
	try:
		return os.path.getmtime(config.LAST_DEPOSIT_FILE)
	except Exception as err:
		log(err)
		return time.time()

def shouldDeposit():
	return getIP() == config.ALLOW_DEPOSIT_IP and
		   datetime.datetime.today().day == 1 and
		   time.time() - getLastDepositTime() > config.DEPOSIT_THRESHOLD

def touch(fname, times=None):
	with open(fname, 'a'):
		os.utime(fname, times)

def stripTrailingZeroes(s):
	return s.rstrip('0')

def normalize(m):
	normalized = {}
	total = float(sum(m.values()))
	for k in m:
		normalized[k] = float(m[k]) / total
	return normalized

#########################
####  LOGGER THINGS  ####
#########################
tab = "   "
logs = []

def log(s):
	message = "[%d] %s" % (int(time.time()), s)
	logs.append(message)
	print message

def logBalance(balance):
	if balance:
		log("Balance:")
		for c in balance:
			log(tab + c + ": " + str(balance[c]))
	else:
		log("##### No balance found")

def logBalances(coinValuesUSD, totalBalance, balances):
	log("")
	log("Balances")
	log("")
	totalUSD = sum(map(lambda c : totalBalance[c] * coinValuesUSD[c], totalBalance.keys()))
	# log("TOTAL BALANCE: {:>9.2f}".format(totalUSD))
	coins = sorted(totalBalance.keys(), key=lambda c: totalBalance[c] * coinValuesUSD[c], reverse=True)
	headers = "{}{:<5} | {:<8} ".format(tab, "Coin", "$/c")
	for b in balances:
		headers += "|| {:<14} | {:<5} ($) ".format(b, b)
	headers += "|| {:<14} | {:<5} ($) | (%) ".format("Total", "Total")
	log(headers)
	log(re.sub('[^|]', '-', headers))
	for c in coins:
		coinValueUSD = coinValuesUSD[c]
		row = "{}{:>5} | {:>8.2f} ".format(tab, c, coinValueUSD)
		for b in balances:
			if c in balances[b]:
				row += "|| {:>14.8f} | {:>9.2f} ".format(balances[b][c], balances[b][c] * coinValueUSD)
			else:
				row += "|| {:>14} | {:>9} ".format("", "")
		v = totalBalance[c] * coinValueUSD
		row += "|| {:>14.8f} | {:>9.2f} | {:>4.1f}".format(totalBalance[c], v, v * 100 / totalUSD)
		log(row)
	log("")

def logAllocation(coinValuesUSD, allocation, balance, targets):
	log("")
	log("Allocations")
	log("")
	sortedAllocation = sorted(allocation.items(), key=lambda x: x[1], reverse=True)
	headers = "{}{:<5} | {:<4} | {:<14} | {:<14} | {:<14} | {:<9} | {:<9} | {:<9}".format(
		tab,
		"Coin",
		"(%)",
		"Have (#)",
		"Target (#)",
		"Diff",
		"Have ($)",
		"Target ($)",
		"Diff")
	log(headers)
	log(re.sub('[^|]', '-', headers))
	for c in sortedAllocation:
		cv = coinValuesUSD[c[0]]
		if c[0] in targets:
			target = targets[c[0]]
			diff = balance.get(c[0], 0) - target
			log("{}{:>5} | {:>4.1f} | {:>14.8f} | {:>14.8f} | {:>14.8f} | {:>9.2f} | {:>10.2f} | {:>9.2f}".format(
				tab,
				c[0],
				c[1] * 100,
				balance.get(c[0], 0),
				target,
				diff,
				balance.get(c[0], 0) * cv,
				target * cv,
				diff * cv))
		else:
			log("{}{:>5} | {:>4.1f} | {:>14.8f} | {:>14} | {:>14} | {:>9.2f} | {:>10} | {:>9}".format(
				tab,
				c[0],
				c[1] * 100,
				balance.get(c[0], 0),
				"",
				"",
				balance.get(c[0], 0) * cv,
				"",
				""))
		# log(tab + c[0] + ": " + str(c[1]))

def logOrders(coinValuesUSD, orders):
	log("")
	log("Ordered orders:")
	for (c, diff) in orders:
		log("{:<4} {: 13.8f} {:>5} ({: 8.2f})".format(
			"Sell" if diff < 0 else "Buy",
			diff if diff > 0 else diff * -1,
			c,
			diff * coinValuesUSD[c]))
	log("")

def sendEmail(balanceUSD):
	dt = datetime.datetime.fromtimestamp(time.time()).strftime('%Y-%m-%d-%H%M%S')
	filename = "log-" + dt + ".txt"
	return requests.post(
		config.MAILGUN_URL,
		auth=("api", config.MAILGUN_KEY),
		files=[("attachment", (filename, "\n".join(logs)))],
		data={"from": config.MAILGUN_EMAIL,
			  "to": "Joseph Lee <z.joseph.lee.z@gmail.com>",
			  "subject": "Crypto Log [{:>9.2f}]".format(balanceUSD),
			  "html": "<html><pre><code>" + "\n".join(logs) + "</code></pre></html>"})

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
	st = Decimal(quantity) * price
	db.cursor().execute(
		"INSERT INTO trade_log(timestamp, exchange, type, quantity, price, subtotal, fee, total) VALUES (?,?,?,?,?,?,?,?)",
		(int(time.time()), exchange, ttype, float(quantity), float(price), float(st), float(fee), float(st) + float(fee)))
	db.commit()

###########################
######  CMC METHODS  ######
###########################

TICKER_URL = "https://api.coinmarketcap.com/v1/ticker?limit=%d"
TICKER_LIMIT = 200

def getCoinValuesUSD():
	log("GET: " + (TICKER_URL % TICKER_LIMIT))
	response = requests.get(TICKER_URL % TICKER_LIMIT)
	values = {}
	allDetails = {}
	for details in response.json():
		values[details["symbol"]] = Decimal(str(details["price_usd"]))
		allDetails[details['symbol']] = details
	return values, allDetails

############################
######  GDAX METHODS  ######
############################

# Create custom authentication for Exchange
class CoinbaseExchangeAuth(AuthBase):
	def __init__(self, api_key, secret_key, passphrase):
		self.api_key = api_key
		self.secret_key = secret_key
		self.passphrase = passphrase

	def __call__(self, request):
		timestamp = str(time.time())
		message = timestamp + request.method + request.path_url + (request.body or '')
		hmac_key = base64.b64decode(self.secret_key)
		signature = hmac.new(hmac_key, message, hashlib.sha256)
		signature_b64 = signature.digest().encode('base64').rstrip('\n')

		request.headers.update({
			'CB-ACCESS-SIGN': signature_b64,
			'CB-ACCESS-TIMESTAMP': timestamp,
			'CB-ACCESS-KEY': self.api_key,
			'CB-ACCESS-PASSPHRASE': self.passphrase,
			'Content-Type': 'application/json'
		})
		return request

GDAX_API_URL = 'https://api.gdax.com/'
auth = CoinbaseExchangeAuth(
	config.GDAX_API_KEY,
	config.GDAX_API_SECRET,
	config.GDAX_API_PASSPHRASE)

def makeGDAXGetRequest(endpoint):
	url = GDAX_API_URL + endpoint
	log("GET: " + url)
	response = requests.get(url, auth=auth)
	log("     " + str(response.json()))
	return response.json()

def makeGDAXPostRequest(endpoint, data):
	url = GDAX_API_URL + endpoint
	log("POST: " + url)
	log("      " + str(data))

	response = requests.post(url, auth=auth, data=json.dumps(data))
	log("      " + str(response.json()))
	return response.json()

def getGDAXBalance():
	j = makeGDAXGetRequest('accounts')
	balance = {}
	balanceUSD = 0

	for details in j:
		# if float(details["balance"]) > 0 and details["currency"] != 'USD':
		# 	balance[details["currency"]] = Decimal(stripTrailingZeroes(details["balance"]))
		balance[details["currency"]] = Decimal(stripTrailingZeroes(details["balance"]))
		if details["currency"] == 'USD':
			balanceUSD = Decimal(stripTrailingZeroes(details["balance"]))
	return balance, balanceUSD

def makeGDAXDeposit(usdAmount, bankId):
	log("")
	log("Making deposit")
	j = makeGDAXPostRequest('deposits/payment-method', {
		"amount": '%.2f' % usdAmount,
		"currency": "USD",
		"payment_method_id": str(bankId)})
	log("Deposit result: {}".format(str(j)))
	log("")

def getGDAXBankAccountId():
	j = makeGDAXGetRequest('payment-methods')
	for account in j:
		if account["type"] == "ach_bank_account":
			return account["id"]

def getGDAXMarket(exchange):
	j = makeGDAXGetRequest('products/' + exchange + '/ticker')
	print j
	return j

def placeGDAXLimitSell(market, quantity, rate, timeout):
	return placeGDAXOrder("sell", market, quantity, rate, timeout)

def placeGDAXLimitBuy(market, quantity, rate, timeout):
	return placeGDAXOrder("buy", market, quantity, rate, timeout)

def placeGDAXOrder(side, product, size, price, timeout):
	r = makeGDAXPostRequest('orders', {
		"size": "{:.8f}".format(size),
		"price": "{:.2f}".format(price.quantize(Decimal('0.01'))),
		"side": side,
		"product_id": product})
	print r
	oid = r['id']
	log("Created order with id: " + oid)
	time.sleep(timeout)
	orderDetails = getGDAXOrderDetails(oid)
	if orderDetails['status'] not in ['done', 'settled']:
		cancelGDAXOrder(oid)
		time.sleep(5)
		return False
	else:
		# TODO: log trade
		return True

def getGDAXOrderDetails(id):
	j = makeGDAXGetRequest('orders/' + id)
	print j
	return j

###########################
####  BITTREX METHODS  ####
###########################

def makeBTRXGetRequest(endpoint, urlargs, args):
	nonce = int(time.time());
	url = ("{}?apikey={}&nonce={}&" + urlargs).format(
		endpoint,
		config.BITTREX_API_KEY,
		nonce,
		*args)
	log("GET: " + url)

	sign = hmac.new(config.BITTREX_API_SECRET, url, hashlib.sha512)
	response = requests.get(url, headers={'apisign': sign.hexdigest()})
	log("     " + str(response.json()))
	if not response.json()["success"]:
		log(response.json()["message"])
		return None
	return response.json()['result']

# ex: getMarket("BTC-ARK")
# returns : {u'Ask': 0.00098, u'Bid': 0.00097114, u'Last': 0.00098}
def getBTRXMarket(exchange):
	return makeBTRXGetRequest(
		"https://bittrex.com/api/v1.1/public/getticker",
		"market={}",
		[exchange])

def getBTRXOrderDetails(uuid):
	return makeBTRXGetRequest(
		"https://bittrex.com/api/v1.1/account/getorder",
		"uuid={}",
		[uuid])

def getBTRXBalance():
	r = makeBTRXGetRequest(
		"https://bittrex.com/api/v1.1/account/getbalances",
		"",
		[])
	balances = {}
	for details in r if r else []:
		if details["Balance"] > 0:
			balances[details["Currency"]] = Decimal(str(details["Balance"]))
	return balances

def cancelBTRXOrder(uuid):
	return makeBTRXGetRequest(
		"https://bittrex.com/api/v1.1/market/cancel",
		"uuid={}",
		[uuid]) != None

# optional market string
def getBTRXOpenOrders():
	return makeBTRXGetRequest(
		"https://bittrex.com/api/v1.1/market/getopenorders",
		"",
		[])

def placeBTRXOrder(ttype, url, market, quantity, rate, timeout):
	r = makeBTRXGetRequest(
		url,
		"market={}&quantity={:.8f}&rate={:.8f}",
		[market, quantity, rate])
	uuid = r['uuid']
	log("Created order with uuid: " + uuid)
	time.sleep(timeout)
	orderDetails = getBTRXOrderDetails(uuid)
	if orderDetails['IsOpen']:
		cancelBTRXOrder(uuid)
		time.sleep(5)
		return False
	else:
		insertTrade(market, ttype, quantity, rate, orderDetails['CommissionPaid'])
		return True

def placeBTRXLimitSell(market, quantity, rate, timeout):
	return placeBTRXOrder("LIMIT SELL", "https://bittrex.com/api/v1.1/market/selllimit", market, quantity, rate, timeout)

def placeBTRXLimitBuy(market, quantity, rate, timeout):
	return placeBTRXOrder("LIMIT BUY", "https://bittrex.com/api/v1.1/market/buylimit", market, quantity, rate, timeout)

def getDepositAddress(currency):
	return makeBTRXGetRequest(
		"https://bittrex.com/api/v1.1/account/getdepositaddress",
		"currency={}",
		[currency])["Address"]

def getWithdrawalHistory(currency):
	return makeBTRXGetRequest(
		"https://bittrex.com/api/v1.1/account/getwithdrawalhistory",
		"currency={}",
		[currency])

def makeWithdrawal(currency, amount, address):
	return makeBTRXGetRequest(
		"https://bittrex.com/api/v1.1/account/withdraw",
		"currency={}&quantity={}&address={}",
		[currency, amount, address])

def withdraw(currency, amount, address):
	r = makeWithdrawal(currency, amount, address)
	uuid = r['uuid']
	log("Created withdrawal with uuid: " + uuid)
	transactionDone = False
	while not transactionDone:
		time.sleep(60)
		history = getWithdrawalHistory(currency)
		for h in history:
			if uuid == h['PaymentUuid'] and not h['PendingPayment']:
				# TODO: log to db
				# insertTransfer('Coinbase', )
				# h['TxCost']
# def insertTransfer(type, amt_usd, amt_btc, fee_usd, fee_btc):
				transactionDone = True
				break
	log("Withdrawal finished")

########################
####  LOGIC THINGS  ####
########################

# Return map of symbol to value percentage
def getAllocation(coinDetails, balance):
	# equal distribution of top 20
	allocation = {}

	for coin in balance.keys():
		allocation[coin] = 0

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
		if allocation[coin] > 0:
			coinPriceUSD = coinValuesUSD[coin]
			targetValueUSD = Decimal(allocation[coin]) * pv
			targetCoinAmount = targetValueUSD / coinPriceUSD
			targets[coin] = targetCoinAmount
	return targets

def getBalanceTotal(coinValuesUSD, balance):
	total = 0
	for c in balance:
		total += balance[c] * coinValuesUSD[c]
	return total

def combineBalances(balanceList):
	balance = {}
	for b in balanceList:
		for c in b:
			if c in balance:
				balance[c] += b[c]
			else:
				balance[c] = b[c]
	return balance

def getMarketValues(coin):
	if config.COIN_EXCHANGES[coin] == 'GDAX':
		details = getGDAXMarket(coin + "-USD")
		if details:
			return (Decimal(details['ask']), Decimal(details['bid']))
	elif config.COIN_EXCHANGES[coin] == 'BTRX':
		details = getBTRXMarket("BTC-" + coin)
		if details:
			print "lkjalksjlkdjf"
			return (Decimal(details['Ask']), Decimal(details['Bid']))
	return (None, None)

def placeLimitSell(coin, amount, rate, timeout):
	if config.COIN_EXCHANGES[coin] == 'GDAX':
		return placeGDAXLimitSell(coin + "-USD", amount, rate, timeout)
	elif config.COIN_EXCHANGES[coin] == 'BTRX':
		return placeBTRXLimitSell("BTC-" + coin, amount, rate, timeout)
	else:
		return False

def placeLimitBuy(coin, amount, rate, timeout):
	if config.COIN_EXCHANGES[coin] == 'GDAX':
		return placeGDAXLimitBuy(coin + "-USD", amount, rate, timeout)
	elif config.COIN_EXCHANGES[coin] == 'BTRX':
		return placeBTRXLimitBuy("BTC-" + coin, amount, rate, timeout)
	else:
		return False

def tryToExecuteOrder(coin, amount):
	log("")
	log("------------------------------------------")
	log("Try to execute order for {: 13.8f} {}".format(amount, coin))
	(ask, bid) = getMarketValues(coin)
	if not ask:
		log("No market found for {}".format(coin))
		log("------------------------------------------")
		return
	log("market values, ask: {:13.8f}, bid: {:13.8f}".format(ask, bid))
	step = (ask - bid) / Decimal(config.ORDER_RETRIES)

	tryRate = ask if amount > 0 else bid
	i = 0
	success = False
	while i < config.ORDER_RETRIES:
		log("### Try {} at {: 13.8f}".format(i, tryRate))
		# TODO - re-look up market?
		if amount < 0 and placeLimitSell(coin, abs(amount), tryRate, config.ORDER_TIMEOUT):
			success = True
			break
		elif amount > 0 and placeLimitBuy(coin, amount, tryRate, config.ORDER_TIMEOUT):
			success = True
			break
		tryRate -= (step if amount < 0 else (-1 * step))
		i += 1
	log("")
	log("Order successful: {}".format(success))
	log("------------------------------------------")

def generateOrders(coinValuesUSD, balance, targets):
	log("")
	orders = []
	for c in targets:
		if c == "USD":
			continue
		o = checkOrder(c, coinValuesUSD, balance, targets)
		if o: orders.append(o)
	orders = sorted(orders, key = lambda o: o[1] * coinValuesUSD[o[0]])
	return orders

def checkOrder(c, coinValuesUSD, balance, targets):
	target = Decimal(targets[c])
	have = Decimal(balance.get(c, 0))
	diff = Decimal(target - have)
	value = abs(diff) * coinValuesUSD[c]
	bigEnoughValue = value > config.REBALANCE_THRESHOLD_VALUE
	bigEnoughRatio = abs(diff) > have * Decimal(config.REBALANCE_THRESHOLD_RATIO)
	if bigEnoughValue and bigEnoughRatio:
		return [c, diff]
	else:
		log("Too small of an order for {} ({: 13.8f} | {: 13.8f}), skipping".format(c, value, (abs(diff) / have) if have > 0 else 0))
		return None

def checkOrdersForBTCTransfer(coinValuesUSD, orders, gdaxBalance, btrxBalance):
	start = btrxBalance['BTC'] * coinValuesUSD['BTC']
	least = 0
	for o in orders:
		coin = o[0]
		if config.COIN_EXCHANGES[coin] == 'BTRX':
			start -= o[1] * coinValuesUSD[coin]
			least = min(start, least)
	# If least is negative, that means BTRX needs more BTC
	if least < 0:
		if least + (gdaxBalance['BTC'] * coinValuesUSD['BTC']) < 0:
			# TODO: we don't have enough BTC period
			log("Not enough BTC period")
		else:
			# TODO: need to transfer 'least' BTC from gdax to btrx
			# POST /withdrawals/crypto
			log("Need to transfer BTC")
	else:
		log("No need to transfer BTC")

#####################
####  MAIN FLOW  ####
#####################

parser=argparse.ArgumentParser()
parser.add_argument('--prod', help='Make changes')

if __name__ == "__main__":
	log("Starting")
	log("")
	args=parser.parse_args()
	makeChanges = args.prod

	# Get values of coins
	coinValuesUSD, allDetails = getCoinValuesUSD()
	coinValuesUSD['USD'] = Decimal("1.0")
	btcToUSD = coinValuesUSD['BTC']

	if shouldDeposit():
		if makeChanges:
			bankId = getGDAXBankAccountId()
			makeGDAXDeposit(config.MONTHLY_DEPOSIT_AMOUNT, bankId)
			touch(config.LAST_DEPOSIT_FILE)
		else:
			log("[[Skipped making deposit]]")

	# Get balances
	gdaxBalance, gdaxUSD = getGDAXBalance()
	btrxBalance = getBTRXBalance()
	balance = combineBalances([gdaxBalance, btrxBalance])
	balanceUSD = getBalanceTotal(coinValuesUSD, balance)

	logBalances(coinValuesUSD, balance, {"GDAX": gdaxBalance, "BTRX": btrxBalance})
	log("TOTAL BALANCE: {:>9.2f}".format(balanceUSD))

	# Get allocations
	allocation = getAllocation(allDetails, balance)
	targets = getTargetAmounts(coinValuesUSD, allocation, balanceUSD)
	logAllocation(coinValuesUSD, allocation, balance, targets)

	orders = generateOrders(coinValuesUSD, balance, targets)
	logOrders(coinValuesUSD, orders)

	checkOrdersForBTCTransfer(coinValuesUSD, orders, gdaxBalance, btrxBalance)

	if makeChanges:
		log("")
		log("Executing orders")
		for (c, diff) in orders:
			tryToExecuteOrder(c, diff)
	else:
		log("[[Skipped making orders]]")

	if makeChanges:
		sendEmail(balanceUSD)

	log("")
	log("Finished")
