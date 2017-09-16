import requests
import time
import sqlite3
import os
import json
import threading

import config

def log(s):
	if True:
		print "[%d] %s" % (int(time.time()), str(s))

TICKER_URL = "https://api.coinmarketcap.com/v1/ticker?limit=%d"
TICKER_LIMIT = 200
TICKER_INTERVAL = 5 * 60

INIT_SCRIPT = """
DROP TABLE IF EXISTS responses;
DROP TABLE IF EXISTS currency_data;
CREATE TABLE responses (
	id INTEGER PRIMARY KEY,
	timestamp INTEGER,
	code INTEGER,
	response TEXT
);
CREATE TABLE currency_data (
	symbol TEXT,
	timestamp INTEGER,
	id TEXT,
	name TEXT,
	last_updated INTEGER,
	price_btc REAL,
	price_usd REAL,
	rank INTEGER,
	percent_change_1h REAL,
	percent_change_24h REAL,
	percent_change_7d REAL,
	available_supply REAL,
	total_supply REAL,
	volume_24h_usd REAL,
	market_cap_usd REAL
);
"""

columns = [
	'symbol',
	'timestamp',
	'id',
	'name',
	'last_updated',
	'price_btc',
	'price_usd',
	'rank',
	'percent_change_1h',
	'percent_change_24h',
	'percent_change_7d',
	'available_supply',
	'total_supply',
	'volume_24h_usd',
	'market_cap_usd'
]

ERRORS_TO_TRACK = 100
errors = []
def trackStatus(r):
	errors.append(r)
	if len(errors) > ERRORS_TO_TRACK:
		errors.pop(0)
	if sum(errors) > ERRORS_TO_TRACK * 0.5:
		log("High error count: " + str(sum(errors)))

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

def getTicker(n):
	try:
		timestamp = int(time.time())
		response = requests.get(TICKER_URL % n)
	except requests.exceptions.RequestException as e:
		log("Error making request")
		log(e)
		return timestamp, None
	return timestamp, response

def saveParsedTickerResponse(db, timestamp, response):
	for details in response.json():
		values = {}
		values['timestamp'] = timestamp
		for c in columns:
			if c == 'timestamp':
				continue
			elif c == 'volume_24h_usd':
				values[c] = details['24h_volume_usd']
			else:
				values[c] = details[c]

		try:
			db.cursor().execute(
				"INSERT INTO currency_data(%s) VALUES (%s)" % (
					",".join(values.keys()), 
					",".join("?" * len(values))), 
				values.values())
		except Exception as e:
			log("Error saving data to database")
			log(e)
			trackStatus(1)
		else:
			db.commit()

def saveRawTickerResponse(db, timestamp, response):
	db.cursor().execute(
		"INSERT INTO responses(timestamp, code, response) VALUES (?, ?, ?)", 
		(timestamp, response.status_code, response.content))
	db.commit()

while 1:
	log("Making request")
	timestamp, response = getTicker(TICKER_LIMIT)
	if response:
		db = getDb(config.DB_FILE_TICKER)
		saveRawTickerResponse(db, timestamp, response)
		try:
			saveParsedTickerResponse(db, timestamp, response)
		except Exception as e:
			log("Error parsing data")
			log(e)
			trackStatus(5)
		else:
			trackStatus(0)
			log("Success")
		db.close()
	else:
		trackStatus(1)
	time.sleep(TICKER_INTERVAL)