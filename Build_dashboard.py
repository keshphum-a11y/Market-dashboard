import datetime as dt
import time
from io import StringIO

import pandas as pd
import requests
import yfinance as yf

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
LT, GT, Q = chr(60), chr(62), chr(34)
PERIODS = [10, 20, 50, 100, 200]
EXTRA = {'Dollar Index': 'DX-Y.NYB', 'Brent Crude': 'BZ=F', 'India VIX': '^INDIAVIX', 'USD/INR': 'INR=X'}

LEGEND = [
    ('FII, DII, Client, Pro', 'FII is foreign institutions, DII is domestic institutions (mutual funds, insurers), Client is retail and other traders, Pro is proprietary trading desks of brokers.'),
    ('Long and Short', 'Long means contracts bought, betting on a rise. Short means contracts sold, betting on a fall. Net is long minus short.'),
    ('Index futures vs stock futures', 'Index futures track Nifty or Bank Nifty. Stock futures track single shares. This data is by category, not by stock name.'),
    ('Index options vs stock options', 'Calls and puts on indices or on single shares. Long and short here mean contracts bought or sold, called or put combined.'),
    ('Open interest (OI)', 'Number of contracts still open. Rising OI with rising price usually means fresh buying; rising OI with falling price usually means fresh selling.'),
    ('Put-call ratio (PCR)', 'Put OI divided by call OI. Above 1 is often read as bullish support, below 0.7 as bearish.'),
    ('Max pain', 'The strike where option buyers collectively lose the most. Prices often drift toward it near expiry.'),
    ('SMA and EMA', 'Moving averages of closing price over N days. SMA is the plain average; EMA weights recent days more. Price above them suggests an uptrend.'),
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.nseindia.com/',
}


def tag(name, content='', cls=''):
    c = (' class=' + Q + cls + Q) if cls else ''
    return LT + name + c + GT + str(content) + LT + '/' + name + GT


def fmt(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ''
    if isinstance(v, (int, float)):
        return format(v, ',.2f') if isinstance(v, float) and abs(v) < 1000 and v != int(v) else format(int(round(v)), ',')
    return str(v)


def colour(v):
    if isinstance(v, (int, float)) and not pd.isna(v):
        return 'pos' if v > 0 else ('neg' if v < 0 else '')
    return ''


def table_html(df, colour_cols=()):
    if df is None or len(df) == 0:
        return tag('p', 'Data could not be fetched this morning.', 'na')
    head = tag('tr', ''.join(tag('th', c) for c in df.columns))
    body = ''
    for _, r in df.iterrows():
        cells = ''
        for c in df.columns:
            cells += tag('td', fmt(r[c]), colour(r[c]) if c in colour_cols else '')
        body += tag('tr', cells)
    return tag('table', head + body)


def note(lines):
    if not lines:
        return ''
    return tag('div', tag('b', 'Analysis') + tag('ul', ''.join(tag('li', x) for x in lines)), 'analysis')


def nse_get(url, tries=3):
    s = requests.Session()
    s.headers.update(HEADERS)
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


# ---------------- participant-wise derivatives (FII, DII, Client, Pro) ----------------
def load_participant_file(d):
    url = ('https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_'
           + d.strftime('%d%m%Y') + '.csv')
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code == 200 and len(r.text) > 100:
            df = pd.read_csv(StringIO(r.text), skiprows=1)
            df.columns = [c.strip() for c in df.columns]
            df['Client Type'] = df['Client Type'].astype(str).str.strip()
            return df
    except Exception:
        pass
    return None


def last_two_files():
    found = []
    for back in range(0, 12):
        d = dt.datetime.now(IST) - dt.timedelta(days=back)
        if d.weekday() >= 5:
            continue
        df = load_participant_file(d)
        if df is not None:
            found.append((d.strftime('%d %b %Y'), df))
        if len(found) == 2:
            break
    return found


GROUPS = [
    ('Index Futures', 'Future Index Long', 'Future Index Short'),
    ('Stock Futures', 'Future Stock Long', 'Future Stock Short'),
    ('Index Call Options', 'Option Index Call Long', 'Option Index Call Short'),
    ('Index Put Options', 'Option Index Put Long', 'Option Index Put Short'),
    ('Stock Call Options', 'Option Stock Call Long', 'Option Stock Call Short'),
    ('Stock Put Options', 'Option Stock Put Long', 'Option Stock Put Short'),
]


def participant_table(files, who):
    (d_now, now), prev = files[0], (files[1] if len(files) > 1 else None)
    rows = []
    row_now = now[now['Client Type'] == who]
    row_prev = prev[1][prev[1]['Client Type'] == who] if prev else None
    if len(row_now) == 0:
        return None
    for name, lcol, scol in GROUPS:
        try:
            lg, sh = float(row_now[lcol].iloc[0]), float(row_now[scol].iloc[0])
            item = {'Segment': name, 'Long': lg, 'Short': sh, 'Net (Long - Short)': lg - sh,
                    'Long/Short ratio': round(lg / sh, 2) if sh else None}
            if row_prev is not None and len(row_prev):
                plg, psh = float(row_prev[lcol].iloc[0]), float(row_prev[scol].iloc[0])
                item['Net previous day'] = plg - psh
                item['Change in net'] = (lg - sh) - (plg - psh)
            rows.append(item)
        except Exception:
            continue
    return pd.DataFrame(rows) if rows else None


def participant_analysis(df, who):
    lines = []
    if df is None:
        return lines
    for _, r in df.iterrows():
        if 'Change in net' in df.columns and pd.notna(r.get('Change in net')):
            ch = r['Change in net']
            side = 'added net long exposure' if ch > 0 else 'added net short exposure'
            if abs(ch) > 0:
                lines.append(who + ' in ' + r['Segment'] + ': ' + side + ' of ' + format(int(abs(ch)), ',') + ' contracts versus the previous day. Net position is now ' + ('long' if r['Net (Long - Short)'] > 0 else 'short') + '.')
    return lines[:6]


# ---------------- FII / DII cash ----------------
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


def cash_analysis(df):
    lines = []
    if df is None:
        return lines
    for _, r in df.iterrows():
        side = 'bought' if r['Net (Rs cr)'] > 0 else 'sold'
        lines.append(str(r['Category']) + ' net ' + side + ' Rs ' + format(abs(r['Net (Rs cr)']), ',.0f') + ' crore in the cash market.')
    return lines


# ---------------- option chain (Nifty, Sensex) ----------------
def option_chain_nse(symbol='NIFTY'):
    data = nse_get('https://www.nseindia.com/api/option-chain-indices?symbol=' + symbol)
    if not data:
        return None, None
    rows = []
    for rec in data['records']['data']:
        ce, pe = rec.get('CE', {}), rec.get('PE', {})
        rows.append({'Expiry': rec['expiryDate'], 'Strike': rec['strikePrice'],
                     'Call OI': ce.get('openInterest', 0), 'Call Chg OI': ce.get('changeinOpenInterest', 0),
                     'Put OI': pe.get('openInterest', 0), 'Put Chg OI': pe.get('changeinOpenInterest', 0)})
    return pd.DataFrame(rows), data['records']['underlyingValue']


def oi_summary(df, spot):
    near = df[df['Expiry'] == df['Expiry'].iloc[0]].copy()
    call_oi, put_oi = near['Call OI'].sum(), near['Put OI'].sum()
    call_chg, put_chg = near['Call Chg OI'].sum(), near['Put Chg OI'].sum()
    pain = {}
    for k in near['Strike']:
        cl = ((k - near['Strike']).clip(lower=0) * near['Call OI']).sum()
        pl = ((near['Strike'] - k).clip(lower=0) * near['Put OI']).sum()
        pain[k] = cl + pl
    top_call = near.loc[near['Call OI'].idxmax(), 'Strike']
    top_put = near.loc[near['Put OI'].idxmax(), 'Strike']
    return {
        'Spot': round(float(spot), 2) if spot else None,
        'Nearest expiry': near['Expiry'].iloc[0],
        'Total Call OI (today)': call_oi, 'Total Put OI (today)': put_oi,
        'Call OI previous day': call_oi - call_chg, 'Put OI previous day': put_oi - put_chg,
        'Change in Call OI': call_chg, 'Change in Put OI': put_chg,
        'PCR today': round(float(put_oi / max(call_oi, 1)), 2),
        'PCR previous day': round(float((put_oi - put_chg) / max(call_oi - call_chg, 1)), 2),
        'Max pain': min(pain, key=pain.get),
        'Highest Call OI strike (resistance)': top_call,
        'Highest Put OI strike (support)': top_put,
    }, near


def option_chain_sensex():
    # BSE's feed is less reliable than NSE's. If it fails, the page shows a note.
    try:
        h = dict(HEADERS)
        h['Referer'] = 'https://www.bseindia.com/'
        r = requests.get('https://api.bseindia.com/BseIndiaAPI/api/DerivOptionChain_IV/w?Expiry=&scrip_cd=1&strprice=0', headers=h, timeout=20)
        if r.status_code != 200:
            return None, None
        js = r.json()
        rows = []
        for rec in js.get('Table', []):
            rows.append({'Expiry': rec.get('End_TimeStamp', 'near'), 'Strike': float(rec['Strike_Price']),
                         'Call OI': float(rec.get('C_OI', 0) or 0), 'Call Chg OI': float(rec.get('C_Chg_OI', 0) or 0),
                         'Put OI': float(rec.get('P_OI', 0) or 0), 'Put Chg OI': float(rec.get('P_Chg_OI', 0) or 0)})
        if not rows:
            return None, None
        df = pd.DataFrame(rows)
        mid = df['Strike'].median()
        return df, mid
    except Exception:
        return None, None


def oi_compare(nifty, sensex):
    rows = []
    for key in nifty:
        rows.append({'Metric': key, 'Nifty 50': nifty.get(key), 'Sensex': sensex.get(key) if sensex else None})
    return pd.DataFrame(rows)


def oi_analysis(name, s):
    lines = []
    if not s:
        return lines
    pcr, ppcr = s['PCR today'], s['PCR previous day']
    lines.append(name + ' PCR moved from ' + str(ppcr) + ' to ' + str(pcr) + (', so put writing is growing relative to calls, a mildly supportive sign.' if pcr > ppcr else ', so call writing is growing relative to puts, a mildly cautious sign.'))
    lines.append(name + ' highest put OI is at ' + format(int(s['Highest Put OI strike (support)']), ',') + ' (a likely support zone) and highest call OI is at ' + format(int(s['Highest Call OI strike (resistance)']), ',') + ' (a likely resistance zone).')
    lines.append(name + ' max pain is ' + format(int(s['Max pain']), ',') + ' against spot ' + str(s['Spot']) + '.')
    return lines


# ---------------- SMA / EMA only ----------------
def ma_table(symbol):
    try:
        c = yf.Ticker(symbol).history(period='2y')['Close'].dropna()
        last = float(c.iloc[-1])
        rows = []
        for n in PERIODS:
            sma = float(c.rolling(n).mean().iloc[-1])
            ema = float(c.ewm(span=n, adjust=False).mean().iloc[-1])
            rows.append({'Period (days)': n, 'SMA': round(sma, 2), 'Price vs SMA (%)': round((last / sma - 1) * 100, 2),
                         'EMA': round(ema, 2), 'Price vs EMA (%)': round((last / ema - 1) * 100, 2)})
        return round(last, 2), pd.DataFrame(rows)
    except Exception:
        return None, None


def ma_analysis(name, last, df):
    if df is None:
        return []
    above = [int(r['Period (days)']) for _, r in df.iterrows() if r['Price vs SMA (%)'] > 0]
    s = name + ' closed at ' + format(last, ',.2f') + ', above its ' + (', '.join(str(a) for a in above) if above else 'none of the') + ' day SMAs. The more averages price holds above, the stronger the trend.'
    return [s]


# ---------------- extra context ----------------
def extra_table():
    rows = []
    for name, sym in EXTRA.items():
        try:
            h = yf.Ticker(sym).history(period='10d')['Close'].dropna()
            last, prev = float(h.iloc[-1]), float(h.iloc[-2])
            rows.append({'Name': name, 'Last': round(last, 2), 'Change': round(last - prev, 2), 'Change %': round((last / prev - 1) * 100, 2)})
        except Exception:
            pass
    return pd.DataFrame(rows) if rows else None


def extra_analysis(df):
    lines = []
    if df is None:
        return lines
    for _, r in df.iterrows():
        n, c = r['Name'], r['Change %']
        if n == 'Dollar Index':
            lines.append('Dollar Index ' + ('rose' if c > 0 else 'fell') + ' ' + str(abs(c)) + '%. A stronger dollar usually weighs on the rupee and on FII flows into India.')
        if n == 'Brent Crude':
            lines.append('Brent crude ' + ('rose' if c > 0 else 'fell') + ' ' + str(abs(c)) + '%. India imports most of its oil, so higher crude is generally a headwind.')
        if n == 'India VIX':
            lines.append('India VIX ' + ('rose' if c > 0 else 'fell') + ' ' + str(abs(c)) + '%. Rising VIX means options are pricing bigger moves.')
    return lines


CSS = ('body{font-family:Segoe UI,Arial;background:#0f172a;color:#e2e8f0;margin:0;padding:16px}'
       'h1{margin:0 0 4px 0}h2{border-left:5px solid #38bdf8;padding-left:10px;margin-top:34px}'
       'h3{color:#38bdf8;margin:18px 0 4px 0}'
       'table{border-collapse:collapse;width:100%;margin:8px 0;font-size:14px;background:#111c33;border-radius:8px;overflow:hidden}'
       'th{background:#1e293b;color:#38bdf8;text-align:left;padding:9px}'
       'td{padding:8px;border-bottom:1px solid #1e293b}tr:nth-child(even) td{background:#0f1a30}'
       '.pos{color:#22c55e;font-weight:600}.neg{color:#ef4444;font-weight:600}'
       '.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}'
       '.card{background:#1e293b;border-radius:10px;padding:12px;font-size:14px}'
       '.analysis{background:#13233f;border-left:4px solid #f59e0b;border-radius:6px;padding:8px 14px;margin:8px 0 14px 0;font-size:14px}'
       '.na{color:#f59e0b}')


def main():
    now = dt.datetime.now(IST).strftime('%d %b %Y, %H:%M IST')
    page = LT + 'html' + GT + LT + 'head' + GT + LT + 'meta charset=' + Q + 'utf-8' + Q + GT
    page += LT + 'meta name=' + Q + 'viewport' + Q + ' content=' + Q + 'width=device-width,initial-scale=1' + Q + GT
    page += tag('title', 'Morning Market Dashboard') + tag('style', CSS) + LT + '/head' + GT + LT + 'body' + GT
    page += tag('h1', 'Morning Market Dashboard') + tag('p', 'Updated ' + now)
    page += tag('h2', 'How to read this page')
    page += LT + 'div class=' + Q + 'grid' + Q + GT + ''.join(tag('div', tag('b', t) + LT + 'br' + GT + d, 'card') for t, d in LEGEND) + LT + '/div' + GT

    cash = fii_dii_cash()
    page += tag('h2', 'FII and DII cash market flows') + table_html(cash, ('Net (Rs cr)',)) + note(cash_analysis(cash))

    files = last_two_files()
    if files:
        page += tag('h2', 'Derivatives positions by participant (' + files[0][0] + (' vs ' + files[1][0] if len(files) > 1 else '') + ')')
        for who, label in [('FII', 'FII (foreign institutions)'), ('DII', 'DII (domestic institutions)'), ('Client', 'Client (retail and others)'), ('Pro', 'Pro (proprietary desks)')]:
            t = participant_table(files, who)
            page += tag('h3', label) + table_html(t, ('Net (Long - Short)', 'Net previous day', 'Change in net')) + note(participant_analysis(t, who))
    else:
        page += tag('h2', 'Derivatives positions by participant') + tag('p', 'NSE participant file could not be fetched.', 'na')

    n_df, n_spot = option_chain_nse('NIFTY')
    s_df, s_spot = option_chain_sensex()
    page += tag('h2', 'Open interest: Nifty 50 vs Sensex (previous day and current day)')
    if n_df is not None:
        n_sum, n_near = oi_summary(n_df, n_spot)
        s_sum = None
        if s_df is not None:
            try:
                s_sum, s_near = oi_summary(s_df, s_spot)
            except Exception:
                s_sum = None
        page += table_html(oi_compare(n_sum, s_sum))
        if s_sum is None:
            page += tag('p', 'Sensex option data could not be fetched from BSE this morning.', 'na')
        page += note(oi_analysis('Nifty', n_sum) + oi_analysis('Sensex', s_sum))
        around = n_near[(n_near['Strike'] > n_spot - 500) & (n_near['Strike'] < n_spot + 500)][['Strike', 'Call OI', 'Call Chg OI', 'Put OI', 'Put Chg OI']]
        page += tag('h3', 'Nifty strike-wise open interest near spot') + table_html(around, ('Call Chg OI', 'Put Chg OI'))
    else:
        page += tag('p', 'NSE option chain could not be fetched this morning.', 'na')

    page += tag('h2', 'Moving averages')
    for name, sym in [('Nifty 50', '^NSEI'), ('Sensex', '^BSESN')]:
        last, df = ma_table(sym)
        page += tag('h3', name) + table_html(df, ('Price vs SMA (%)', 'Price vs EMA (%)')) + note(ma_analysis(name, last, df))

    ex = extra_table()
    page += tag('h2', 'Dollar, crude and volatility') + table_html(ex, ('Change', 'Change %')) + note(extra_analysis(ex))

    page += tag('p', 'Data from NSE, BSE and Yahoo Finance public pages. Figures may be delayed or missing. For study only, not trading advice.', 'na')
    page += LT + '/body' + GT + LT + '/html' + GT
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(page)
    print('index.html written at', now)


if __name__ == '__main__':
    main()
