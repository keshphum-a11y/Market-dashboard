 import datetime as dt
import time
from io import StringIO

import pandas as pd
import requests
import yfinance as yf

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
LT, GT, Q = chr(60), chr(62), chr(34)

GLOBAL = {
    'S&P 500': '^GSPC', 'Nasdaq': '^IXIC', 'Dow Jones': '^DJI',
    'Russell 2000': '^RUT', 'US VIX': '^VIX',
    'Dollar Index': 'DX-Y.NYB', 'Brent Crude': 'BZ=F', 'WTI Crude': 'CL=F',
    'Gold': 'GC=F', 'US 10Y Yield': '^TNX', 'US 2Y Yield': '^IRX',
    'USD/INR': 'INR=X', 'Nikkei 225': '^N225', 'Hang Seng': '^HSI',
    'Shanghai': '000001.SS', 'FTSE 100': '^FTSE', 'DAX': '^GDAXI',
}
INDIA = {'Nifty 50': '^NSEI', 'Sensex': '^BSESN',
         'Bank Nifty': '^NSEBANK', 'India VIX': '^INDIAVIX'}
SECTORS = {
    'Nifty Bank': '^NSEBANK', 'Nifty IT': '^CNXIT', 'Nifty Auto': '^CNXAUTO',
    'Nifty Pharma': '^CNXPHARMA', 'Nifty Metal': '^CNXMETAL',
    'Nifty FMCG': '^CNXFMCG', 'Nifty Energy': '^CNXENERGY',
    'Nifty Realty': '^CNXREALTY', 'Nifty PSU Bank': '^CNXPSUBANK',
}
HEAVYWEIGHTS = {
    'Reliance': 'RELIANCE.NS', 'HDFC Bank': 'HDFCBANK.NS',
    'ICICI Bank': 'ICICIBANK.NS', 'Infosys': 'INFY.NS', 'TCS': 'TCS.NS',
    'Bharti Airtel': 'BHARTIARTL.NS', 'ITC': 'ITC.NS', 'L&T': 'LT.NS',
}
PERIODS = [10, 20, 50, 100, 200]

LEGEND = [
    ('SMA and EMA', 'Moving averages of the closing price over N days. SMA is the plain average; EMA gives more weight to recent days so it reacts faster. Price above them suggests an uptrend, below suggests a downtrend. The 200-day is the classic long-term trend line.'),
    ('RSI', 'A 0 to 100 gauge (14 days) of how fast price has risen or fallen. Above 70 is often called overbought, below 30 oversold, near 50 neutral.'),
    ('MACD', 'The gap between the 12 and 26 day EMAs, with a 9 day signal line. MACD above the signal line means momentum is turning up; below means it is fading.'),
    ('Bollinger Bands', 'A 20 day average with bands 2 standard deviations above and below. Price near the upper band is stretched up, near the lower band stretched down.'),
    ('ATR', 'Typical daily price swing in points over 14 days. It shows how volatile the market is, not the direction.'),
    ('Pivot, R1, S1', 'Support and resistance levels calculated from the previous high, low and close. Traders watch them as possible turning points.'),
    ('Put-call ratio (PCR)', 'Total put open interest divided by total call open interest. Above 1 is often read as bullish, below 0.7 as bearish.'),
    ('Max pain', 'The strike where option buyers collectively lose the most. Prices often drift toward it near expiry.'),
    ('Open interest (OI)', 'Number of contracts still open. Big OI at a strike marks a level traders are defending.'),
    ('India VIX', 'How much movement the market expects over the next 30 days. High means nervous, low means calm.'),
    ('FII and DII', 'Foreign and domestic institutional investors. Net FII buying usually supports the market; net selling pressures it.'),
]


def snapshot(symbols):
    rows = []
    for name, sym in symbols.items():
        try:
            h = yf.Ticker(sym).history(period='10d')['Close'].dropna()
            last, prev = float(h.iloc[-1]), float(h.iloc[-2])
            rows.append({'Name': name, 'Last': round(last, 2),
                         'Change': round(last - prev, 2),
                         'Change %': round((last / prev - 1) * 100, 2)})
        except Exception:
            rows.append({'Name': name, 'Last': None, 'Change': None, 'Change %': None})
    return pd.DataFrame(rows)


NSE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.nseindia.com/',
}


def nse_get(url, tries=3):
    s = requests.Session()
    s.headers.update(NSE_HEADERS)
    for _ in range(tries):
        try:
            s.get('https://www.nseindia.com', timeout=15)
            r = s.get(url, timeout=20)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(3)
    return None


def option_chain(symbol='NIFTY'):
    data = nse_get('https://www.nseindia.com/api/option-chain-indices?symbol=' + symbol)
    if not data:
        return None, None
    rows = []
    for rec in data['records']['data']:
        ce, pe = rec.get('CE', {}), rec.get('PE', {})
        rows.append({
            'Expiry': rec['expiryDate'], 'Strike': rec['strikePrice'],
            'Call OI': ce.get('openInterest', 0), 'Call Chg OI': ce.get('changeinOpenInterest', 0),
            'Put OI': pe.get('openInterest', 0), 'Put Chg OI': pe.get('changeinOpenInterest', 0),
        })
    return pd.DataFrame(rows), data['records']['underlyingValue']


def pcr_and_max_pain(df):
    near = df[df['Expiry'] == df['Expiry'].iloc[0]].copy()
    pcr = near['Put OI'].sum() / max(near['Call OI'].sum(), 1)
    pain = {}
    for k in near['Strike']:
        call_loss = ((k - near['Strike']).clip(lower=0) * near['Call OI']).sum()
        put_loss = ((near['Strike'] - k).clip(lower=0) * near['Put OI']).sum()
        pain[k] = call_loss + put_loss
    return round(float(pcr), 2), min(pain, key=pain.get), near


def fii_dii_cash():
    data = nse_get('https://www.nseindia.com/api/fiidiiTradeReact')
    if not data:
        return None
    rows = []
    for r in data:
        rows.append({'Category': r['category'], 'Date': r['date'],
                     'Buy (Rs cr)': float(r['buyValue']), 'Sell (Rs cr)': float(r['sellValue']),
                     'Net (Rs cr)': float(r['netValue'])})
    return pd.DataFrame(rows)


def fii_derivatives():
    for back in range(0, 6):
        d = dt.datetime.now(IST) - dt.timedelta(days=back)
        url = ('https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_'
               + d.strftime('%d%m%Y') + '.csv')
        try:
            r = requests.get(url, headers=NSE_HEADERS, timeout=20)
            if r.status_code == 200 and len(r.text) > 100:
                df = pd.read_csv(StringIO(r.text), skiprows=1)
                df.columns = [c.strip() for c in df.columns]
                fii = df[df['Client Type'].str.strip() == 'FII'].T.reset_index()
                fii.columns = ['Position', 'FII']
                return d.strftime('%d %b %Y'), fii
        except Exception:
            continue
    return None, None


def advance_decline():
    data = nse_get('https://www.nseindia.com/api/allIndices')
    if not data:
        return None
    for row in data['data']:
        if row['index'] == 'NIFTY 50':
            return ('Advances ' + str(row.get('advances')) + ', Declines '
                    + str(row.get('declines')) + ', Unchanged ' + str(row.get('unchanged')))
    return None


def technicals(symbol):
    try:
        h = yf.Ticker(symbol).history(period='2y')
        c, hi, lo = h['Close'], h['High'], h['Low']
        last = float(c.iloc[-1])
        out = {'Last close': round(last, 2)}
        for n in PERIODS:
            sma = float(c.rolling(n).mean().iloc[-1])
            ema = float(c.ewm(span=n, adjust=False).mean().iloc[-1])
            out['SMA ' + str(n)] = round(sma, 2)
            out['EMA ' + str(n)] = round(ema, 2)
            out['Price vs SMA ' + str(n) + ' (%)'] = round((last / sma - 1) * 100, 2)
        delta = c.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
        out['RSI 14'] = round(float(100 - 100 / (1 + gain.iloc[-1] / loss.iloc[-1])), 1)
        macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
        sig = macd.ewm(span=9, adjust=False).mean()
        out['MACD'] = round(float(macd.iloc[-1]), 2)
        out['MACD signal'] = round(float(sig.iloc[-1]), 2)
        mid, sd = c.rolling(20).mean(), c.rolling(20).std()
        out['Bollinger upper'] = round(float((mid + 2 * sd).iloc[-1]), 2)
        out['Bollinger lower'] = round(float((mid - 2 * sd).iloc[-1]), 2)
        tr = pd.concat([hi - lo, (hi - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
        out['ATR 14'] = round(float(tr.rolling(14).mean().iloc[-1]), 2)
        pivot = float((hi.iloc[-1] + lo.iloc[-1] + c.iloc[-1]) / 3)
        out['Pivot'] = round(pivot, 2)
        out['R1'] = round(2 * pivot - float(lo.iloc[-1]), 2)
        out['S1'] = round(2 * pivot - float(hi.iloc[-1]), 2)
        out['52 week high'] = round(float(hi.tail(252).max()), 2)
        out['52 week low'] = round(float(lo.tail(252).min()), 2)
        return out
    except Exception:
        return {}


def tag(name, content='', cls=''):
    c = (' class=' + Q + cls + Q) if cls else ''
    return LT + name + c + GT + str(content) + LT + '/' + name + GT


def colour(v):
    if isinstance(v, (int, float)) and not pd.isna(v):
        return 'pos' if v > 0 else ('neg' if v < 0 else '')
    return ''


def table_html(df, change_cols=('Change', 'Change %', 'Net (Rs cr)')):
    if df is None or len(df) == 0:
        return tag('p', 'Data could not be fetched this morning.', 'na')
    head = tag('tr', ''.join(tag('th', c) for c in df.columns))
    body = ''
    for _, r in df.iterrows():
        cells = ''
        for c in df.columns:
            v = r[c]
            blank = v is None or (isinstance(v, float) and pd.isna(v))
            cells += tag('td', '' if blank else v, colour(v) if c in change_cols else '')
        body += tag('tr', cells)
    return tag('table', head + body)


def analysis(us, tech, pcr, max_pain, spot):
    lines = []

    def chg(name):
        row = us[us['Name'] == name]
        if len(row) and pd.notna(row['Change %'].iloc[0]):
            return float(row['Change %'].iloc[0])
        return None

    spx, vix = chg('S&P 500'), chg('US VIX')
    dxy, brent, y10 = chg('Dollar Index'), chg('Brent Crude'), chg('US 10Y Yield')
    if spx is not None:
        tone = 'positive' if spx > 0.3 else ('negative' if spx < -0.3 else 'flat')
        lines.append('US markets closed ' + tone + ' (S&P 500 ' + str(spx) + '%). A ' + tone + ' US close usually sets a matching tone for the Indian open, though India often reacts to its own news.')
    if dxy is not None and dxy > 0.3:
        lines.append('The dollar index rose ' + str(dxy) + '%. A stronger dollar tends to weigh on the rupee and can prompt FII selling in emerging markets.')
    if brent is not None and abs(brent) > 1.5:
        lines.append('Brent crude moved ' + str(brent) + '%. India imports most of its oil, so sharp rises are generally negative for the rupee and inflation, and falls generally supportive.')
    if y10 is not None and y10 > 1:
        lines.append('The US 10 year yield jumped ' + str(y10) + '%. Rising US yields often draw money away from emerging markets.')
    if vix is not None and vix > 5:
        lines.append('US VIX spiked ' + str(vix) + '%, a sign of rising global fear.')
    if tech:
        last = tech['Last close']
        above = [n for n in PERIODS if last > tech['SMA ' + str(n)]]
        names = ', '.join(str(n) for n in above) if above else 'none of its'
        lines.append('Nifty is above its ' + names + ' day averages. The more averages price sits above, the stronger the trend.')
        r = tech['RSI 14']
        state = 'stretched up (overbought)' if r > 70 else ('stretched down (oversold)' if r < 30 else 'in a neutral zone')
        lines.append('RSI is ' + str(r) + ', which is ' + state + '.')
        mood = 'above' if tech['MACD'] > tech['MACD signal'] else 'below'
        lines.append('MACD is ' + mood + ' its signal line, so short-term momentum is ' + ('building' if mood == 'above' else 'fading') + '.')
    if pcr is not None:
        lines.append('Nifty put-call ratio is ' + str(pcr) + ' and max pain is at ' + str(max_pain) + ' against a spot of ' + str(spot) + '.')
    lines.append('This is an educational overview of the data, not trading advice.')
    return lines


CSS = ('body{font-family:Segoe UI,Arial;background:#0f172a;color:#e2e8f0;margin:0;padding:16px}'
       'h2{border-left:5px solid #38bdf8;padding-left:10px;margin-top:30px}'
       'table{border-collapse:collapse;width:100%;margin:10px 0;font-size:14px}'
       'th{background:#1e293b;color:#38bdf8;text-align:left;padding:8px}'
       'td{padding:7px;border-bottom:1px solid #1e293b}'
       '.pos{color:#22c55e;font-weight:600}.neg{color:#ef4444;font-weight:600}'
       '.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}'
       '.card{background:#1e293b;border-radius:10px;padding:12px;font-size:14px}'
       '.na{color:#f59e0b}.analysis li{margin:8px 0}')


def main():
    now = dt.datetime.now(IST).strftime('%d %b %Y, %H:%M IST')
    us = snapshot(GLOBAL)
    india = snapshot(INDIA)
    sectors = snapshot(SECTORS)
    heavy = snapshot(HEAVYWEIGHTS)
    tech_nifty = technicals('^NSEI')
    tech_sensex = technicals('^BSESN')

    pcr = max_pain = spot = oi_table = None
    try:
        chain, spot = option_chain('NIFTY')
        if chain is not None:
            pcr, max_pain, near = pcr_and_max_pain(chain)
            around = near[(near['Strike'] > spot - 500) & (near['Strike'] < spot + 500)]
            oi_table = around[['Strike', 'Call OI', 'Call Chg OI', 'Put OI', 'Put Chg OI']]
    except Exception:
        pass

    cash = fii_dii_cash()
    fii_date, fii_deriv = fii_derivatives()
    breadth = advance_decline()
    notes = analysis(us, tech_nifty, pcr, max_pain, spot)

    legend = ''.join(tag('div', tag('b', t) + LT + 'br' + GT + d, 'card') for t, d in LEGEND)
    if tech_nifty or tech_sensex:
        tech_df = pd.DataFrame([tech_nifty, tech_sensex], index=['Nifty 50', 'Sensex']).T.reset_index()
        tech_df.columns = ['Indicator', 'Nifty 50', 'Sensex']
    else:
        tech_df = None

    page = LT + 'html' + GT + LT + 'head' + GT
    page += LT + 'meta charset=' + Q + 'utf-8' + Q + GT
    page += LT + 'meta name=' + Q + 'viewport' + Q + ' content=' + Q + 'width=device-width,initial-scale=1' + Q + GT
    page += tag('title', 'Morning Market Dashboard') + tag('style', CSS)
    page += LT + '/head' + GT + LT + 'body' + GT
    page += tag('h1', 'Morning Market Dashboard') + tag('p', 'Updated ' + now)
    page += tag('h2', 'How to read this page') + LT + 'div class=' + Q + 'grid' + Q + GT + legend + LT + '/div' + GT
    page += tag('h2', 'Overnight US markets and global cues') + table_html(us)
    page += tag('h2', 'India: indices and volatility') + table_html(india)
    page += tag('h2', 'FII and DII cash market flows') + table_html(cash)
    page += tag('h2', 'FII derivatives positions (' + str(fii_date) + ')') + table_html(fii_deriv, change_cols=())
    page += tag('h2', 'Nifty option chain near spot (PCR ' + str(pcr) + ', max pain ' + str(max_pain) + ')') + table_html(oi_table, change_cols=('Call Chg OI', 'Put Chg OI'))
    page += tag('h2', 'Market breadth (Nifty 50)') + tag('p', breadth if breadth else 'Not available.')
    page += tag('h2', 'Sector performance') + table_html(sectors)
    page += tag('h2', 'Heavyweight stocks') + table_html(heavy)
    page += tag('h2', 'Technical levels') + table_html(tech_df, change_cols=())
    page += tag('h2', 'Analysis') + tag('ul', ''.join(tag('li', x) for x in notes), 'analysis')
    page += tag('p', 'Data from Yahoo Finance and NSE public pages. Figures may be delayed or missing. For study only, not trading advice.', 'na')
    page += LT + '/body' + GT + LT + '/html' + GT

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(page)
    print('index.html written at', now)


if __name__ == '__main__':
    main()
