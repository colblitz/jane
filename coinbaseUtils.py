from coinbase.wallet.client import Client
import config

def offerBuy ( params ) :
    client = params['client']
    payment_method = params['payment_method']
    source_currency_symbol = params['source_currency_symbol']
    source_currency_max_buy_price = params['source_currency_max_buy_price']
    target_currency_symbol = params['target_currency_symbol']
    target_currency_amount_to_buy = params['target_currency_amount_to_buy']

    result = None
    success = False
    print '-----'
    # print client.get_buy_price( currency = source_currency_symbol )
    print source_currency_symbol
    print target_currency_symbol
    print '-----'
    buy_price = float( client.get_buy_price( currency=source_currency_symbol ).amount )

    if  buy_price <= source_currency_max_buy_price :
        result = account.buy(
            amount=target_currency_amount_to_buy,
            currency=target_currency_symbol,
            payment_method=payment_method
        )
        success = True
    else :
        result = 'Failed because the maximum buy price was %f %s, but the actual buy price was %f %s.' % (
            source_currency_max_buy_price,
            source_currency_symbol,
            buy_price,
            source_currency_symbol
        )
    return { 'result' : result , 'success' : success}

def offerSell ( params ) :
    client = params['client']
    payment_method = params['payment_method']
    source_currency_symbol = params['source_currency_symbol']
    source_currency_amount_to_sell = params['source_currency_amount_to_sell']
    target_currency_symbol = params['target_currency_symbol']
    target_currency_min_aquisition_amount = params['target_currency_min_aquisition_amount']

    result = None
    success = False
    sell_price = float( client.get_sell_price( currency = target_currency_symbol ).amount )

    if  sell_price <= target_currency_min_aquisition_amount :
        result = account.sell(
            amount=source_currency_amount_to_sell,
            currency=source_currency_symbol,
            payment_method=payment_method
        )
        success = True
    else :
        result = 'Failed because the minimum sale price was %f %s, but the sell price was only %f %s.' % (
            target_currency_min_aquisition_amount,
            target_currency_symbol,
            sell_price,
            target_currency_symbol
        )
    return { 'result' : result , 'success' : success}

def go():
    print 'start'

    client = Client(
        config.COINBASE_KEY,
        config.COINBASE_SECRET,
        api_version='2017-04-13'
    )

    account = client.get_primary_account()
    payment_methods = client.get_payment_methods().data
    if( len(payment_methods) < 1 ):
        print 'No payment methods.'
        return
    payment_method = payment_methods[0].id

    # (Right now .001 BTC is about equal to 3.65 USD.)
    # Buy .001 BTC if we can do so for 3 USD or less.
    buyParams = {
        'client' : client,
        'payment_method' : payment_method,
        'source_currency_symbol' : 'USD',
        'source_currency_max_buy_price' : 3,
        'target_currency_symbol' : 'BTC',
        'target_currency_amount_to_buy' : '.001'
    }
    # print buyParams
    buyResult = offerBuy(buyParams)
    print buyResult

    # (Right now .001 BTC is about equal to 3.65 USD.)
    # Sell .001 BTC if we can get at least 5 USD for doing so.
    sellParams = {
        'client' : client,
        'payment_method' : payment_method,
        'source_currency_symbol' : 'BTC',
        'source_currency_amount_to_sell' :'.001',
        'target_currency_symbol' : 'USD',
        'target_currency_min_aquisition_amount' : 5,
    }
    sellResult = offerSell(sellParams)
    print sellResult


go()
