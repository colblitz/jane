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
from decimal import *
from pprint import pprint
from requests.auth import AuthBase

from coinbase.wallet.client import Client

import config

TICKER_URL = "https://api.coinmarketcap.com/v1/ticker?limit=%d"
TICKER_LIMIT = 200

logs = []
def log(s):
	message = "[%d] %s" % (int(time.time()), s)
	logs.append(message)
	print message

def getIP():
	return socket.gethostbyname(socket.gethostname())

def getLastDepositTime():
	try:
		return os.path.getmtime(config.LAST_DEPOSIT_FILE)
	except Exception as err:
		log(err)
		return time.time()

def shouldDeposit():
	# return True
	# a = getLastDepositTime()
	# return time.time() - a > 10
	return getIP() == config.ALLOW_DEPOSIT_IP and time.time() - getLastDepositTime() > DEPOSIT_THRESHOLD

def touch(fname, times=None):
    with open(fname, 'a'):
        os.utime(fname, times)

def isProd():
	try:
		return config.IS_PROD
	except:
		return False

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

api_url = 'https://api.gdax.com/'
auth = CoinbaseExchangeAuth(
	config.GDAX_API_KEY,
	config.GDAX_API_SECRET,
	config.GDAX_API_PASSPHRASE)

def makeGDAXGetRequest(endpoint):
	url = api_url + endpoint
	log("GET: " + url)
	return requests.get(url, auth=auth).json()

def makeGDAXPostRequest(endpoint, data):
	url = api_url + endpoint
	log("POST: " + url)
	log("      " + str(data))
	return requests.post(url, auth=auth, data=json.dumps(data)).json()

def stripTrailingZeroes(s):
	return s.rstrip('0')

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

# def getGDAXUSDBalance():
# 	j = makeGDAXGetRequest('accounts')
# 	for details in j:
# 		if details["currency"] == 'USD':
# 			return Decimal(stripTrailingZeroes(details["balance"]))
# 	return 0

# def getGDAXUSDAccountId():
# 	pass

# print makeGDAXGetRequest('accounts/5e6d08bd-0f31-4e41-a60f-498399a59cd5/ledger')
# print makeGDAXGetRequest('accounts/c8e6dfbc-0a58-4c6c-9601-b36569f25369/ledger')

# GET /accounts/<account-id>/ledger

def getGDAXBankAccountId():
	j = makeGDAXGetRequest('payment-methods')
	for account in j:
		if account["type"] == "ach_bank_account":
			return account["id"]


def getGDAXMarket(exchange):
	j = makeGDAXGetRequest('products/' + exchange + '/ticker')
	return j

def getGDAXOrderDetails():
	pass



# r = requests.get(api_url + 'payment-methods', auth=auth)
# pprint(r.json())

# Get accounts
# r = requests.get(api_url + 'accounts', auth=auth)
# print pprint(r.json())
# [{"id": "a1b2c3d4", "balance":...

# Place an order
# order = {
#     'size': 1.0,
#     'price': 1.0,
#     'side': 'buy',
#     'product_id': 'BTC-USD',
# }
# r = requests.post(api_url + 'orders', json=order, auth=auth)
# print r.json()
# {"id": "0428b97b-bec1-429e-a94c-59992926778d"}


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
		log("Getting coinbase accounts, paymentMethods, addresses")
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

		self.addressesBTC = client.get_addresses(self.accountIdBTC)
		self.addressBTC = self.addressesBTC['data'][0]['address']

	def getBTCBalanceInUSD(self):
		return float(self.accountBTC["native_balance"]["amount"])
	def getBTCBalance(self):
		return float(self.accountBTC["balance"]["amount"])

	def getBTCSellStatus(self, sid):
		log("Getting sell details {}".format(sid))
		details = self.client.get_sell(self.accountIdBTC, sid)
		return details['status']

	def sellBTC(self, amount):
		if amount > self.getBTCBalance():
			log("Not enough in balance to sell - requested {}, have {}".format(amount, self.getBTCBalance()))
			return

		log("Trying to sell {} btc".format(amount))
		sellData = self.client.sell(
			self.accountIdBTC,
			total = str(amount),
			currency = 'BTC',
			payment_method = self.paymentMethodIdUSD)
		log("Sell details: {}".format(sellData))

		sid = sellData['id']
		while self.getBTCSellStatus(sid).lower() != "completed":
			log("Sell {} not completed, sleeping 60 seconds".format(sid))
			time.sleep(60)
		log("Sell complete")

	def sellAllBTC(self):
		amount = self.getBTCBalance()
		self.sellBTC(amount * 0.95)

	def getBTCTransactionStatus(self, tid):
		log("Getting transaction details: {}".format(tid))
		details = self.client.get_transaction(self.accountIdBTC, tid)
		return details['status']

	def getBTCTransactionFee(self, tid):
		pass

	def sendBTC(self, address, amount):
		if amount > self.getBTCBalance():
			log("Not enough in balance - requested {}, have {}".format(amount, self.getBTCBalance()))
			return

		log("Trying to send {} btc to {}".format(amount, address))
		transactionData = self.client.send_money(
			self.accountIdBTC,
			to = address,
			amount = str(amount),
			currency = 'BTC',
			idem = uuid.uuid1().hex)
		log("Transaction details: {}".format(transactionData))

		tid = transactionData['id']
		while self.getBTCTransactionStatus(tid).lower() != "completed":
			log("Transaction {} not finished, sleeping 60 seconds".format(tid))
			time.sleep(60)
		# TODO: insertTransfer("coinbase", amount * coinValuesUSD['BTC'], amount, )
		log("Transaction completed")

	def buyBTC(self):
		pass

	def withdrawUSD(self):
		pass

def getAccount():
	return CoinbaseAccount(client)

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
####  OTHER THINGS  ####
########################

def getCoinValuesUSD():
	log("GET: " + (TICKER_URL % TICKER_LIMIT))
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

# def tryToMakeOrder(coin, amount):
# 	marketValues = getMarket("BTC-"+coin)
# 	if marketValues:
# 		ask = marketValues['Ask']
# 		bid = marketValues['Bid']
# 		step = (ask - bid) / float(config.ORDER_RETRIES)

# 		tryRate = ask if amount > 0 else bid
# 		i = 0
# 		success = False
# 		while i < config.ORDER_RETRIES:
# 			log("# Try {} to order {} for coin at {}".format(i, coin, tryRate))
# 			# TODO - re-look up market?
# 			if amount < 0 and placeLimitSell("BTC-" + coin, abs(amount), tryRate, config.ORDER_TIMEOUT):
# 				success = True
# 				break
# 			elif amount > 0 and placeLimitBuy("BTC-" + coin, amount, tryRate, config.ORDER_TIMEOUT):
# 				success = True
# 				break
# 			tryRate -= (step if amount < 0 else (-1 * step))
# 			i += 1
# 		log("Order successful: {}".format(success))
# 	else:
# 		log("No market for {}".format(coin))

# def makeOrders(coinValuesUSD, balance, targets):
# 	for c in targets:
# 		log("-----")
# 		t = Decimal(targets[c])
# 		h = Decimal(balance.get(c, 0))
# 		d = Decimal(t - h)
# 		v = abs(d) * coinValuesUSD[c]
# 		if c == "BTC" or c == "BCH":
# 			# TODO
# 			log("Skipping BTC/BCH for now")
# 			continue
# 		else:
# 			if v > config.REBALANCE_THRESHOLD_VALUE and abs(d) > h * Decimal(config.REBALANCE_THRESHOLD_RATIO):
# 				log("Try to make order for {} of {}".format(c, d))
# 				tryToMakeOrder(c, d)
# 			else:
# 				log("Too small of an order for {}, skipping ({} | {})".format(c, v, (abs(d) / h) if h > 0 else 0))

tab = "   "

def logBalance(balance):
	if balance:
		log("Balance:")
		for c in balance:
			log(tab + c + ": " + str(balance[c]))
	else:
		log("##### No balance found")

def logBalances(coinValuesUSD, totalBalance, balances):
	log("")
	totalUSD = sum(map(lambda c : totalBalance[c] * coinValuesUSD[c], totalBalance.keys()))
	# log("TOTAL BALANCE: {:>9.2f}".format(totalUSD))
	coins = sorted(totalBalance.keys(), key=lambda c: totalBalance[c] * coinValuesUSD[c], reverse=True)
	headers = "{}{:<5} | {:<8} ".format(tab, "Coin", "$/c")
	for b in balances:
		headers += "|| {:<13} | {:<5} ($) ".format(b, b)
	headers += "|| {:<13} | {:<5} ($) | (%) ".format("Total", "Total")
	log(headers)
	log(re.sub('[^|]', '-', headers))
	for c in coins:
		coinValueUSD = coinValuesUSD[c]
		row = "{}{:>5} | {:>8.2f} ".format(tab, c, coinValueUSD)
		for b in balances:
			if c in balances[b]:
				row += "|| {:>13.8f} | {:>9.2f} ".format(balances[b][c], balances[b][c] * coinValueUSD)
			else:
				row += "|| {:>13} | {:>9} ".format("", "")
		v = totalBalance[c] * coinValueUSD
		row += "|| {:>13.8f} | {:>9.2f} | {:>4.1f}".format(totalBalance[c], v, v * 100 / totalUSD)
		log(row)
	log("")

def logAllocation(coinValuesUSD, allocation, balance, targets):
	log("")
	sortedAllocation = sorted(allocation.items(), key=lambda x: x[1], reverse=True)
	headers = "{}{:<5} | {:<4} | {:<13} | {:<13} | {:<13} | {:<9} | {:<9} | {:<9}".format(
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
			log("{}{:>5} | {:>4.1f} | {:>13.8f} | {:>13.8f} | {:>13.8f} | {:>9.2f} | {:>10.2f} | {:>9.2f}".format(
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
			log("{}{:>5} | {:>4.1f} | {:>13.8f} | {:>13} | {:>13} | {:>9.2f} | {:>10} | {:>9}".format(
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


def getBalanceTotal(coinValuesUSD, balance):
	total = 0
	for c in balance:
		total += balance[c] * coinValuesUSD[c]
	return total

def logPortfolio(av, sv):
	log("Portfolio Value:")
	log(tab + "Supposed: " + str(sv))
	log(tab + "  Actual: " + str(av))

def valueLogFormat(v, p):
	return "{:.2f} USD, {:.8f} BTC".format(v, v / p)

parser=argparse.ArgumentParser()
parser.add_argument('--persist', help='Make changes')



def combineBalances(balanceList):
	balance = {}
	for b in balanceList:
		for c in b:
			if c in balance:
				balance[c] += b[c]
			else:
				balance[c] = b[c]
	return balance

def sendEmail():
	pass

def tryToMakeGDAXOrder(c, diff):
	log("try to make GDAX order for {: 13.8f} {}".format(diff, c))

def tryToMakeBTRXOrder(c, diff):
	log("try to make BTRX order for {: 13.8f} {}".format(diff, c))
	marketValues = getBTRXMarket("BTC-"+coin)
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

def getMarketValues(coin):
	if config.COIN_EXCHANGES[coin] == 'GDAX':
		details = getGDAXMarket(coin + "-USD")
		return (details['ask'], details['bid'])
	elif config.COIN_EXCHANGES[coin] == 'BTRX':
		details = getBTRXMarket("BTC-" + coin)
		return (details['Ask'], details['Bid'])
	else:
		return (None, None)

def placeLimitSell(coin, amount, rate, timeout):
	if config.COIN_EXCHANGES[coin] == 'GDAX':
		### TODO
		pass
	elif config.COIN_EXCHANGES[coin] == 'BTRX':
		return placeBTRXLimitSell("BTC-" + coin, amount, rate, timeout)
	else:
		return False

def placeLimitBuy(coin, amount, rate, timeout):
	if config.COIN_EXCHANGES[coin] == 'GDAX':
		### TODO
		pass
	elif config.COIN_EXCHANGES[coin] == 'BTRX':
		return placeBTRXLimitBuy("BTC-" + coin, amount, rate, timeout)
	else:
		return False

def tryToMakeOrder(coin, amount):
	log("try to make order for {: 13.8f} {}".format(diff, coin))
	(ask, bid) = getMarketValues(coin)
	if not ask:
		log("No market found for {}".format(coin))
		return
	step = (ask - bid) / float(config.ORDER_RETRIES)

	tryRate = ask if amount > 0 else bid
	i = 0
	success = False
	while i < config.ORDER_RETRIES:
		log("# Try {} at {}".format(i, tryRate))
		# TODO - re-look up market?
		if amount < 0 and placeLimitSell(coin, abs(amount), tryRate, config.ORDER_TIMEOUT):
			success = True
			break
		elif amount > 0 and placeLimitBuy(coin, amount, tryRate, config.ORDER_TIMEOUT):
			success = True
			break
		tryRate -= (step if amount < 0 else (-1 * step))
		i += 1
	log("Order successful: {}".format(success))

def makeOrders(coinValuesUSD, balance, targets):
	orders = []
	for c in targets:
		o = checkOrder(c, coinValuesUSD, balance, targets)
		if o: orders.append(o)
	orders = sorted(orders, key = lambda o: o[1] * coinValuesUSD[o[0]])

	log("")
	log("Ordered orders:")
	for (c, diff) in orders:
		log("{:>4} {: 13.8f} {:>5} ({})".format(
			"Sell" if diff < 0 else "Buy",
			diff if diff > 0 else diff * -1,
			c,
			diff * coinValuesUSD[c]))
	log("")

	for (c, diff) in orders:
		tryToMakeOrder(c, diff)

# def makeGDAXOrders(coinValuesUSD, balance, targets):
# 	orders = []
# 	for c in ['BTC', 'ETH', 'LTC']:
# 		o = checkOrder("GDAX", c, coinValuesUSD, balance, targets)
# 		if o: orders.append(o)
# 	for (c, diff) in [o for o in orders if o[1] < 0]:
# 		tryToMakeGDAXOrder(c, diff)
# 	for (c, diff) in [o for o in orders if o[1] > 0]:
# 		tryToMakeGDAXOrder(c, diff)

# def makeBTRXOrders(coinValuesUSD, balance, targets):
# 	orders = []
# 	for c in [c for c in targets if c not in ['USD', 'BTC', 'ETH', 'LTC']]:
# 		o = checkOrder("BTRX", c, coinValuesUSD, balance, targets)
# 		if o: orders.append(o)
# 	for (c, diff) in [o for o in orders if o[1] < 0]:
# 		tryToMakeBTRXOrder(c, diff)
# 	for (c, diff) in [o for o in orders if o[1] > 0]:
# 		tryToMakeBTRXOrder(c, diff)

def checkOrder(c, coinValuesUSD, balance, targets):
	target = Decimal(targets[c])
	have = Decimal(balance.get(c, 0))
	diff = Decimal(target - have)
	value = abs(diff) * coinValuesUSD[c]
	bigEnoughValue = value > config.REBALANCE_THRESHOLD_VALUE
	bigEnoughRatio = abs(diff) > have * Decimal(config.REBALANCE_THRESHOLD_RATIO)
	if bigEnoughValue or bigEnoughRatio:
		return [c, diff]
	else:
		log("Too small of an order for {} ({} | {}), skipping".format(c, value, (abs(diff) / have) if have > 0 else 0))
		return None

if __name__ == "__main__":
	log("Starting")
	log("")
	args=parser.parse_args()
	makeChanges = args.persist

	# if True:
	# 	sys.exit(1)

	# Get values of coins
	coinValuesUSD, allDetails = getCoinValuesUSD()
	coinValuesUSD['USD'] = Decimal("1.0")
	btcToUSD = coinValuesUSD['BTC']

	if shouldDeposit():
		if makeChanges:
			bankId = getGDAXBankAccountId()
			makeGDAXDeposit(config.WEEKLY_DEPOSIT_AMOUNT, bankId)
			touch(config.LAST_DEPOSIT_FILE)
		else:
			log("[[Skipped making deposit]]")

	# Get balances
	gdaxBalance, gdaxUSD = getGDAXBalance()
	btrxBalance = getBTRXBalance()
	balance = combineBalances([gdaxBalance, btrxBalance])
	logBalances(coinValuesUSD, balance, {"GDAX": gdaxBalance, "BTRX": btrxBalance})

	balanceUSD = getBalanceTotal(coinValuesUSD, balance)
	totalUSD = balanceUSD + gdaxUSD

	log("TOTAL BALANCE: {:>9.2f}".format(balanceUSD))
	log("  NEW BALANCE: {:>9.2f}".format(totalUSD))

	allocation = getAllocation(allDetails, balance)
	targets = getTargetAmounts(coinValuesUSD, allocation, totalUSD)
	logAllocation(coinValuesUSD, allocation, balance, targets)

	makeOrders(coinValuesUSD, balance, targets)
	# makeGDAXOrders(coinValuesUSD, balance, targets)
	# makeBTRXOrders(coinValuesUSD, balance, targets)

	if True:
		sys.exit(1)

	if makeChanges:
		makeGDAXOrders(coinValuesUSD, balance, targets)
		makeBTRXOrders(coinValuesUSD, balance, targets)
	else:
		log("[[Skipped making orders]]")

	log("")
	log("Finished")

	sendEmail()

	# GDAX - buy BTC, then do ETH/BTC, LTC/BTC


	# 1. Transfer from bank to GDAX - every week
	# 2. Get allocations - BTC/ETH/LTC in GDAX, others in Bittrex


	# 3. Transfer BTC to Bittrex if necessary
	# 4. Buy/sell things
	# 5. Send email



	# Check if there's anything to transfer in
	# coinbase = getAccount()
	# btcTransferThreshold = config.TRANSFER_THRESHOLD / btcToUSD
	# if coinbase.getBTCBalance() > btcTransferThreshold:
	# 	bittrexBTCAddress = getDepositAddress("BTC")
	# 	amount = coinbase.getBTCBalance() * 0.98
	# 	log("Bringing in {}".format(valueLogFormat(Decimal(amount) * btcToUSD, btcToUSD)))
	# 	if makeChanges:
	# 		coinbase.sendBTC(bittrexBTCAddress, amount)
	# 	else:
	# 		log("[[Skipped]]")
	# else:
	# 	log("Nothing to transfer from coinbase ({:.8f})".format(coinbase.getBTCBalance()))

	# Get balance
	# balance = getBittrexBalance()
	# logBalance(balance)

	# actualValue = Decimal(0.0)
	# for c in balance:
	# 	actualValue += balance[c] * coinValuesUSD[c]

	# supposedValue = Decimal(getPortfolioValue())
	# moveAmount = 0
	# profit = actualValue - supposedValue
	# if profit > 0:
	# 	log("Portfolio is over expected by {}".format(valueLogFormat(profit, btcToUSD)))
	# 	if profit > config.TRANSFER_THRESHOLD:
	# 		moveAmount = profit * Decimal(1 - config.PROFIT_RATIO_TO_KEEP)
	# 		keepAmount = profit * Decimal(config.PROFIT_RATIO_TO_KEEP)
	# 		supposedValue += keepAmount
	# 		log("Logging profit of {}".format(valueLogFormat(keepAmount, btcToUSD)))
	# 		if makeChanges:
	# 			insertProfitValue(keepAmount, keepAmount / btcToUSD)
	# 		else:
	# 			log("[[Skipped]]")
	# else:
	# 	log("Portfolio is under expected by {}".format(valueLogFormat(profit, btcToUSD)))

	# logPortfolio(actualValue, supposedValue)

	# Get allocation
	# allocation = getAllocation(allDetails, balance)
	# targets = getTargetAmounts(coinValuesUSD, allocation, supposedValue)
	# logAllocation(allocation, balance, targets)

	# Make orders
	if makeChanges:
		makeOrders(coinValuesUSD, balance, targets)
	else:
		log("[[Skipped]]")

	# TODO: send profits out
	if moveAmount > 0:
		log("Moving profits out to coinbase of {}".format(valueLogFormat(moveAmount, btcToUSD)))
		if makeChanges:
			withdraw("BTC", (profit / coinValuesUSD["BTC"]) * 0.99, coinbase.addressBTC)
			coinbase.sellAllBTC()
		else:
			log("[[Skipped]]")
		# TODO: transfer to bank
	else:
		log("Nothing to move to coinbase")

	log("Finished")

	# Check speculation







