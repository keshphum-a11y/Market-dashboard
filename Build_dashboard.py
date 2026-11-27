import calendar
import collections
import datetime as dt
import email.utils
import html
import io
import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import yfinance as yf

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
LT, GT, Q = chr(60), chr(62), chr(34)
PERIODS = [10, 20, 50, 100, 200]
EXTRA = {'Dollar Index': 'DX-Y.NYB', 'US 10Y Yield': '^TNX', 'Brent Crude': 'BZ=F', 'India VIX': '^INDIAVIX', 'USD/INR': 'INR=X'}
INDICES = [('Nifty 50', '^NSEI'), ('Sensex', '^BSESN')]
WHO = ['FII', 'DII', 'Client', 'Pro']

LEGEND = [
    ('FII, DII, Client, Pro', 'FII is foreign institutions, DII is domestic institutions (mutual funds, insurers), Client is retail and other individual traders, Pro is proprietary trading desks of brokers and arbitrage firms.'),
    ('Long, Short, Net', 'Long means contracts bought, a bet on a rise. Short means contracts sold, a bet on a fall. Net is long minus short. A positive net means the group is net long.'),
    ('Index vs stock segments', 'Index futures and options track Nifty or Bank Nifty. Stock futures and options track single shares. This data is by category, not by stock name.'),
    ('Open interest (OI)', 'Number of contracts still open. Rising OI with a rising price usually means fresh buying; rising OI with a falling price usually means fresh selling.'),
    ('Put-call ratio (PCR)', 'Put OI divided by call OI. Above 1 is often read as bullish support, below 0.7 as bearish. Extreme readings can also signal crowded positioning.'),
    ('Max pain', 'The strike where option buyers collectively lose the most. Prices often drift toward it near expiry.'),
    ('OI walls', 'For the nearest expiry: the strike at or below spot with the largest put OI (a likely floor) and the strike at or above spot with the largest call OI (a likely ceiling).'),
    ('SMA and EMA', 'Moving averages of closing price over N days. SMA is the plain average; EMA weights recent days more. Price above them suggests an uptrend.'),
    ('India VIX and implied move', 'India VIX is the annualised volatility the options market expects. Spot times VIX times the square root of (days / 365) gives a one-standard-deviation expected move for the period; roughly two-thirds of outcomes fall inside it.'),
    ('ATR', 'Average True Range over 14 days: the typical daily price swing in points. It shows volatility, not direction.'),
    ('Option spreads', 'A spread combines a bought and a sold option so the maximum loss is capped. Examples: bull put spread, bear call spread, iron condor.'),
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.nseindia.com/',
}


# ================= small helpers =================
def tag(name, content='', cls=''):
    c = (' class=' + Q + cls + Q) if cls else ''
    return LT + name + c + GT + str(content) + LT + '/' + name + GT


def indian(v, dec=0):
    """Indian digit grouping, for example 1,41,519 and 12,34,567.89."""
    try:
        x = float(v)
    except Exception:
        return str(v)
    if pd.isna(x):
        return ''
    s = format(abs(x), '.' + str(dec) + 'f')
    neg = x < 0 and float(s) != 0
    ip, _, fp = s.partition('.')
    if len(ip) > 3:
        head, tail = ip[:-3], ip[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        ip = ','.join(parts + [tail])
    return ('-' if neg else '') + ip + (('.' + fp) if dec else '')


def n(v):
    return indian(v, 0)


def sgn(v):
    try:
        return ('+' if float(v) > 0 else '') + n(v)
    except Exception:
        return str(v)


def fmt(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ''
    if isinstance(v, (int, float, np.integer, np.floating)):
        v = float(v)
        if pd.isna(v):
            return ''
        if v != int(v) and abs(v) < 1000:
            return indian(v, 2)
        return indian(v, 0)
    return str(v)


def colour(v):
    if isinstance(v, (int, float, np.integer, np.floating)) and not pd.isna(v):
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
    return tag('div', tag('table', head + body), 'scroll')


def note(paras, title='Quick read', bullets=True):
    paras = [p for p in paras if p]
    if not paras:
        return ''
    if bullets:
        body = tag('ul', ''.join(tag('li', p) for p in paras))
    else:
        body = ''.join(tag('p', p) for p in paras)
    return tag('div', tag('b', title) + body, 'analysis')


def rnd(x, step=50):
    return int(round(x / step) * step)


def safe(fn, *args):
    try:
        return fn(*args)
    except Exception:
        return None


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


# ================= FII / DII CASH =================
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


def cash_split(df):
    fii = dii = None
    if df is None:
        return fii, dii
    for _, r in df.iterrows():
        cat = str(r['Category']).upper()
        if 'FII' in cat or 'FPI' in cat:
            fii = float(r['Net (Rs cr)'])
        elif 'DII' in cat:
            dii = float(r['Net (Rs cr)'])
    return fii, dii


def cash_analysis(df):
    fii, dii = cash_split(df)
    out = []
    if fii is not None:
        out.append('FII: net ' + ('bought' if fii > 0 else 'sold') + ' Rs ' + n(abs(fii)) + ' crore. ' +
                   ('Foreign buying is the main fuel for broad rallies and supports the rupee.' if fii > 0 else
                    'Foreign selling drags the heavyweights, weakens the rupee and raises the odds of a soft open.'))
    if dii is not None:
        out.append('DII: net ' + ('bought' if dii > 0 else 'sold') + ' Rs ' + n(abs(dii)) + ' crore. ' +
                   ('Steady domestic SIP money cushions dips.' if dii > 0 else
                    'Domestic selling removes the cushion, which matters most when FIIs are also selling.'))
    if fii is not None and dii is not None:
        if fii < 0 and dii > 0:
            out.append('Net picture: DIIs absorbed about ' + format(dii / abs(fii) * 100 if fii else 0, '.0f') + '% of FII selling. Over 50% usually means the index holds; well under that, dips tend to extend.')
        elif fii > 0 and dii > 0:
            out.append('Net picture: both are buying, the strongest flow backdrop.')
        elif fii > 0 and dii < 0:
            out.append('Net picture: rally depends on foreign money while domestic funds book profit; fragile if global cues turn.')
        else:
            out.append('Net picture: both are selling, the weakest backdrop; bounces tend to fade.')
    if out:
        out.append('Watch: one day is noise; three to five sessions in the same direction is a trend.')
    return out


# ================= PARTICIPANT DERIVATIVES =================
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


def last_files(k=3):
    found = []
    for back in range(0, 16):
        d = dt.datetime.now(IST) - dt.timedelta(days=back)
        if d.weekday() >= 5:
            continue
        df = load_participant_file(d)
        if df is not None:
            found.append((d.strftime('%d %b %Y'), df))
        if len(found) == k:
            break
    return found


GROUPS = [
    ('Index Futures', 'Future Index Long', 'Future Index Short'),
    ('Index Call Options', 'Option Index Call Long', 'Option Index Call Short'),
    ('Index Put Options', 'Option Index Put Long', 'Option Index Put Short'),
    ('Stock Futures', 'Future Stock Long', 'Future Stock Short'),
]

# +1: buying is bullish (futures, calls). -1: buying is bearish (puts).
POLARITY = {'Index Futures': 1, 'Stock Futures': 1, 'Index Call Options': 1,
            'Index Put Options': -1}


def find_col(df, wanted):
    key = wanted.lower().replace(' ', '')
    for c in df.columns:
        if str(c).lower().replace(' ', '') == key:
            return c
    return None


def pull(df, who, lname, sname):
    try:
        r = df[df['Client Type'] == who]
        return float(r[find_col(df, lname)].iloc[0]), float(r[find_col(df, sname)].iloc[0])
    except Exception:
        return None


def participant_data(files):
    if not files:
        return None
    now = files[0][1]
    prev = files[1][1] if len(files) > 1 else None
    prev2 = files[2][1] if len(files) > 2 else None
    out = {}
    for seg, lname, sname in GROUPS:
        for who in WHO:
            a = pull(now, who, lname, sname)
            if not a:
                continue
            lg, sh = a
            d = {'lg': lg, 'sh': sh, 'net': lg - sh, 'plg': None, 'psh': None, 'pnet': None,
                 'chg': None, 'dlg': None, 'dsh': None, 'p2net': None}
            b = pull(prev, who, lname, sname) if prev is not None else None
            if b:
                d['plg'], d['psh'], d['pnet'] = b[0], b[1], b[0] - b[1]
                d['dlg'], d['dsh'] = lg - b[0], sh - b[1]
                d['chg'] = (lg - sh) - (b[0] - b[1])
            c = pull(prev2, who, lname, sname) if prev2 is not None else None
            if c:
                d['p2net'] = c[0] - c[1]
            out[(seg, who)] = d
    return out or None


def cls_for(v, pol):
    try:
        x = float(v) * pol
    except Exception:
        return ''
    if pd.isna(x):
        return ''
    return 'pos' if x > 0 else ('neg' if x < 0 else '')


def move_text(v, up, down):
    if v is None:
        return ''
    if v == 0:
        return 'No change'
    return (up if v > 0 else down) + ' ' + n(abs(v))


def build_table(headers, rows):
    head = tag('tr', ''.join(tag('th', h) for h in headers))
    body = ''.join(tag('tr', ''.join(tag('td', t, c) for t, c in r)) for r in rows)
    return tag('div', tag('table', head + body), 'scroll')


def participant_summary_table(pdata):
    """Net contracts bought (+) or sold (-) today, every participant in every segment."""
    if not pdata:
        return ''
    segs = [g[0] for g in GROUPS]
    rows = []
    any_val = False
    for who in WHO:
        r = [(who, '')]
        for seg in segs:
            d = pdata.get((seg, who))
            if d and d['chg'] is not None:
                any_val = True
                r.append((sgn(d['chg']), cls_for(d['chg'], POLARITY[seg])))
            else:
                r.append(('', ''))
        rows.append(r)
    if not any_val:
        return ''
    return build_table(['Participant'] + segs, rows)


def participant_segment_table(pdata, seg, files):
    pol = POLARITY[seg]
    dates = [f[0] for f in files]
    heads = ['Participant', 'Change in longs', 'Change in shorts',
             'Net bought / sold today', 'Net position ' + dates[0]]
    if len(dates) > 1:
        heads.append('Net position ' + dates[1])
    if len(dates) > 2:
        heads.append('Net position ' + dates[2])
    rows = []
    for who in WHO:
        d = pdata.get((seg, who))
        if not d:
            continue
        r = [(who, ''),
             (move_text(d['dlg'], 'Added', 'Closed'), cls_for(d['dlg'], pol)),
             (move_text(d['dsh'], 'Added', 'Closed'), cls_for(d['dsh'], -pol)),
             (move_text(d['chg'], 'Bought', 'Sold'), cls_for(d['chg'], pol)),
             (sgn(d['net']), cls_for(d['net'], pol))]
        if len(dates) > 1:
            r.append((sgn(d['pnet']) if d['pnet'] is not None else '', cls_for(d['pnet'], pol)))
        if len(dates) > 2:
            r.append((sgn(d['p2net']) if d['p2net'] is not None else '', cls_for(d['p2net'], pol)))
        rows.append(r)
    return build_table(heads, rows) if rows else ''


def share_long(lg, sh):
    t = (lg or 0) + (sh or 0)
    return lg / t * 100 if t else None


def opt_stance(call_net, put_net):
    """Reads the combined index option position of one participant."""
    if call_net is None or put_net is None:
        return None
    if put_net > 0 and call_net < 0:
        return 'bearish'
    if call_net > 0 and put_net < 0:
        return 'bullish'
    if call_net < 0 and put_net < 0:
        return 'seller'
    if call_net > 0 and put_net > 0:
        return 'buyer'
    return 'mixed'


STANCE_TEXT = {
    'bearish': 'net long puts and short calls (bearish)',
    'bullish': 'net long calls and short puts (bullish)',
    'seller': 'net short both calls and puts (premium selling, expects a range)',
    'buyer': 'net long both calls and puts (expects a big move or is hedging)',
    'mixed': 'mixed, no clear direction',
}


def fut_sentence(who, d):
    sl = share_long(d['lg'], d['sh'])
    s = who + ' index futures: long ' + n(d['lg']) + ', short ' + n(d['sh']) + ', net ' + sgn(d['net']) + ' contracts (long share ' + format(sl, '.0f') + '%'
    if d['plg'] is not None:
        psl = share_long(d['plg'], d['psh'])
        s += ', previous day ' + format(psl, '.0f') + '%'
    s += ').'
    return s, sl


ROLE_SHORT = {
    'FII': 'Most-watched group; tends to lead direction.',
    'DII': 'Mostly hedging, not a directional call.',
    'Client': 'Retail crowd; often on the wrong side at turning points.',
    'Pro': 'Arbitrage and market-making; a balancing item.',
}


def participant_analysis(pdata, files=None):
    if not pdata:
        return []
    out = []
    for who in WHO:
        d = pdata.get(('Index Futures', who))
        if not d:
            continue
        sl = share_long(d['lg'], d['sh'])
        s = who + ': index futures net ' + sgn(d['net']) + (' (' + format(sl, '.0f') + '% long)' if sl is not None else '')
        if d['chg'] is not None:
            s += ', moved ' + sgn(d['chg']) + ' today (' + ('bullish' if d['chg'] > 0 else ('bearish' if d['chg'] < 0 else 'flat')) + ')'
        c, pt = pdata.get(('Index Call Options', who)), pdata.get(('Index Put Options', who))
        if c and pt:
            s += '. Options: ' + STANCE_TEXT.get(opt_stance(c['net'], pt['net']), '')
        out.append(s + '. ' + ROLE_SHORT[who])
    nets = {w: pdata[('Index Futures', w)]['net'] for w in WHO if ('Index Futures', w) in pdata}
    if len(nets) >= 3:
        longs = [w for w, v in nets.items() if v > 0]
        shorts = [w for w, v in nets.items() if v < 0]
        t = 'Sides in index futures: long ' + (', '.join(longs) or 'nobody') + '; short ' + (', '.join(shorts) or 'nobody') + '.'
        f, ci = nets.get('FII'), nets.get('Client')
        if f is not None and ci is not None and f * ci < 0:
            t += ' FIIs and Clients disagree; the institutional side has usually been the stronger hand.'
        out.append(t)
    f = pdata.get(('Index Futures', 'FII'))
    if f and f['pnet'] is not None and f['p2net'] is not None and files and len(files) > 2:
        a, b, c = f['p2net'], f['pnet'], f['net']
        trend = 'moving toward long for two sessions, a building signal' if c > b > a else ('moving toward short for two sessions, a building signal' if c < b < a else 'not consistent over two sessions, so treat today as tentative')
        out.append('FII index futures trend (' + sgn(a) + ', ' + sgn(b) + ', ' + sgn(c) + '): ' + trend + '.')
    return out


# ================= OPTION CHAIN / OPEN INTEREST =================
def option_chain_nse(symbol='NIFTY'):
    data = nse_get('https://www.nseindia.com/api/option-chain-indices?symbol=' + symbol)
    if not data:
        return None, None, None
    rows = []
    for rec in data['records']['data']:
        ce, pe = rec.get('CE', {}), rec.get('PE', {})
        rows.append({'Expiry': rec['expiryDate'], 'Strike': rec['strikePrice'],
                     'Call OI': ce.get('openInterest', 0), 'Call Chg OI': ce.get('changeinOpenInterest', 0),
                     'Put OI': pe.get('openInterest', 0), 'Put Chg OI': pe.get('changeinOpenInterest', 0)})
    if not rows:
        return None, None, None
    label = 'Live NSE option chain' + (' (' + str(data['records']['timestamp']) + ')' if data['records'].get('timestamp') else '')
    return pd.DataFrame(rows), data['records']['underlyingValue'], label


def option_chain_sensex():
    try:
        h = dict(HEADERS)
        h['Referer'] = 'https://www.bseindia.com/'
        r = requests.get('https://api.bseindia.com/BseIndiaAPI/api/DerivOptionChain_IV/w?Expiry=&scrip_cd=1&strprice=0', headers=h, timeout=20)
        if r.status_code != 200:
            return None, None, None
        js = r.json()
        rows = []
        for rec in js.get('Table', []):
            rows.append({'Expiry': rec.get('End_TimeStamp', 'near'), 'Strike': float(rec['Strike_Price']),
                         'Call OI': float(rec.get('C_OI', 0) or 0), 'Call Chg OI': float(rec.get('C_Chg_OI', 0) or 0),
                         'Put OI': float(rec.get('P_OI', 0) or 0), 'Put Chg OI': float(rec.get('P_Chg_OI', 0) or 0)})
        if not rows:
            return None, None, None
        df = pd.DataFrame(rows)
        return df, float(df['Strike'].median()), 'Live BSE option chain'
    except Exception:
        return None, None, None


# ---- last-trading-session fallback (exchange end-of-day F&O files, "bhavcopy")
def norm_name(c):
    return ''.join(ch for ch in str(c).lower() if ch.isalnum())


def pick_col(df, *names):
    m = {norm_name(c): c for c in df.columns}
    for nm in names:
        if norm_name(nm) in m:
            return m[norm_name(nm)]
    return None


def read_bhav(content):
    if content[:2] == b'PK':
        return pd.read_csv(io.BytesIO(content), compression='zip')
    return pd.read_csv(io.BytesIO(content))


def bhav_url(d, exch):
    ymd = d.strftime('%Y%m%d')
    if exch == 'BSE':
        return 'https://www.bseindia.com/download/Bhavcopy/Derivative/BhavCopy_BSE_FO_0_0_0_' + ymd + '_F_0000.csv'
    return 'https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_' + ymd + '_F_0000.csv.zip'


def load_bhav(d, exch='NSE'):
    try:
        r = requests.get(bhav_url(d, exch), headers=HEADERS, timeout=30)
        if r.status_code == 200 and len(r.content) > 500:
            return read_bhav(r.content)
    except Exception:
        pass
    return None


def chain_from_bhav(df, symbol):
    sym = pick_col(df, 'TckrSymb', 'Symbol')
    opt = pick_col(df, 'OptnTp', 'Option Type')
    strike = pick_col(df, 'StrkPric', 'Strike Price')
    exp = pick_col(df, 'XpryDt', 'Expiry Date')
    oi = pick_col(df, 'OpnIntrst', 'Open Interest')
    chg = pick_col(df, 'ChngInOpnIntrst', 'Change in OI')
    und = pick_col(df, 'UndrlygPric')
    if None in (sym, opt, strike, exp, oi):
        return None, None
    x = df[(df[sym].astype(str).str.strip() == symbol) & (df[opt].astype(str).str.strip().isin(['CE', 'PE']))].copy()
    if x.empty:
        return None, None
    x['_e'] = pd.to_datetime(x[exp], errors='coerce').dt.strftime('%d-%b-%Y')
    x['_k'] = pd.to_numeric(x[strike], errors='coerce')
    x['_oi'] = pd.to_numeric(x[oi], errors='coerce').fillna(0)
    x['_ch'] = pd.to_numeric(x[chg], errors='coerce').fillna(0) if chg else 0
    x['_t'] = x[opt].astype(str).str.strip()
    x = x.dropna(subset=['_e', '_k'])
    rows = []
    for (e, k), g in x.groupby(['_e', '_k']):
        ce, pe = g[g['_t'] == 'CE'], g[g['_t'] == 'PE']
        rows.append({'Expiry': e, 'Strike': float(k),
                     'Call OI': float(ce['_oi'].sum()), 'Call Chg OI': float(ce['_ch'].sum()),
                     'Put OI': float(pe['_oi'].sum()), 'Put Chg OI': float(pe['_ch'].sum())})
    if not rows:
        return None, None
    spot = None
    if und:
        v = pd.to_numeric(x[und], errors='coerce').dropna()
        spot = float(v.median()) if len(v) else None
    return pd.DataFrame(rows), spot


def bhavcopy_chain(symbol='NIFTY', exch='NSE'):
    for back in range(0, 12):
        d = dt.datetime.now(IST) - dt.timedelta(days=back)
        if d.weekday() >= 5:
            continue
        df = load_bhav(d, exch)
        if df is None:
            continue
        chain, spot = chain_from_bhav(df, symbol)
        if chain is not None:
            return chain, spot, 'Last trading session (' + d.strftime('%d %b %Y') + '), ' + exch + ' F&O end-of-day file'
    return None, None, None


def pick_expiry(df):
    today = dt.datetime.now(IST).date()
    best = None
    for e in df['Expiry'].unique():
        try:
            d = dt.datetime.strptime(str(e), '%d-%b-%Y').date()
        except Exception:
            continue
        if d >= today and (best is None or d < best[0]):
            best = (d, e)
    return best[1] if best else df['Expiry'].iloc[0]


def oi_summary(df, spot):
    near = df[df['Expiry'] == pick_expiry(df)].copy()
    call_oi, put_oi = near['Call OI'].sum(), near['Put OI'].sum()
    call_chg, put_chg = near['Call Chg OI'].sum(), near['Put Chg OI'].sum()
    pain = {}
    for k in near['Strike']:
        cl = ((k - near['Strike']).clip(lower=0) * near['Call OI']).sum()
        pl = ((near['Strike'] - k).clip(lower=0) * near['Put OI']).sum()
        pain[k] = cl + pl
    # walls: largest put OI at or below spot (floor), largest call OI at or above spot (ceiling)
    below = near[near['Strike'] <= spot] if spot else near
    above = near[near['Strike'] >= spot] if spot else near
    if len(below) == 0:
        below = near
    if len(above) == 0:
        above = near
    top_call = above.loc[above['Call OI'].idxmax(), 'Strike']
    top_put = below.loc[below['Put OI'].idxmax(), 'Strike']
    summary = {
        'Spot': round(float(spot), 2) if spot else None,
        'Nearest expiry': near['Expiry'].iloc[0],
        'Total Call OI (today)': call_oi, 'Total Put OI (today)': put_oi,
        'Call OI previous day': call_oi - call_chg, 'Put OI previous day': put_oi - put_chg,
        'Change in Call OI': call_chg, 'Change in Put OI': put_chg,
        'PCR today': round(float(put_oi / max(call_oi, 1)), 2),
        'PCR previous day': round(float((put_oi - put_chg) / max(call_oi - call_chg, 1)), 2),
        'Max pain': float(min(pain, key=pain.get)),
        'Highest Call OI strike (resistance)': float(top_call),
        'Highest Put OI strike (support)': float(top_put),
    }
    return summary, near


def oi_compare(nifty, sensex):
    def col(sm):
        if not sm:
            return None
        return [
            str(sm['Nearest expiry']),
            str(sm['PCR today']) + ' (prev ' + str(sm['PCR previous day']) + ')',
            n(sm['Total Call OI (today)']) + ' (' + sgn(sm['Change in Call OI']) + ')',
            n(sm['Total Put OI (today)']) + ' (' + sgn(sm['Change in Put OI']) + ')',
            n(sm['Highest Put OI strike (support)']),
            n(sm['Highest Call OI strike (resistance)']),
            n(sm['Max pain']),
        ]
    labels = ['Expiry', 'PCR', 'Call OI (change)', 'Put OI (change)', 'Support (put wall)', 'Resistance (call wall)', 'Max pain']
    a, b = col(nifty), col(sensex)
    rows = []
    for i, lab in enumerate(labels):
        rows.append({'Metric': lab, 'Nifty 50': a[i] if a else '', 'Sensex': b[i] if b else 'n/a'})
    return pd.DataFrame(rows)


def oi_analysis(name, s, chg_pct=None):
    if not s:
        return []
    out = []
    spot, pw = s['Spot'], s['Highest Put OI strike (support)']
    cw, mp = s['Highest Call OI strike (resistance)'], s['Max pain']
    pcr, ppcr = s['PCR today'], s['PCR previous day']
    cc, pc = s['Change in Call OI'], s['Change in Put OI']
    if pcr >= 1.3:
        r = 'very put-heavy: supportive, but can mean over-hedging'
    elif pcr >= 1.0:
        r = 'put-leaning: supportive, put writers defend lower levels'
    elif pcr >= 0.7:
        r = 'balanced, no side dominant'
    else:
        r = 'call-heavy: upside is capped, cautious'
    out.append(name + ' PCR ' + str(pcr) + ' (prev ' + str(ppcr) + '), ' + r + '; ' + ('rising, more put writing.' if pcr > ppcr else ('falling, more call writing.' if pcr < ppcr else 'unchanged.')))
    t = name + ' fresh OI: calls ' + sgn(cc) + ', puts ' + sgn(pc) + '. ' + ('Puts are building faster, so a floor is forming.' if pc > cc else 'Calls are building faster, so a ceiling is forming.')
    if chg_pct is not None:
        t += ' Index ' + ('up' if chg_pct >= 0 else 'down') + ' ' + format(abs(chg_pct), '.2f') + '% today.'
    out.append(t)
    if spot:
        down, up = spot - pw, cw - spot
        if down < 0:
            w = 'Spot is below the put wall: the floor is breached, which can accelerate a fall.'
        elif up < 0:
            w = 'Spot is above the call wall: the ceiling is breached, which can accelerate a rise.'
        else:
            w = 'Spot sits between the walls, so a range is likely until one breaks.'
        out.append(name + ' range: support ' + n(pw) + ', resistance ' + n(cw) + ', max pain ' + n(mp) + ' (' + n(abs(mp - spot)) + ' pts ' + ('above' if mp > spot else 'below') + ' spot). ' + w)
    return out


# ================= MOVING AVERAGES =================
HIST_INFO = {}


def parse_nse_history(data):
    """Turn NSE's historical index JSON into a DataFrame of Date, High, Low, Close (or None)."""
    try:
        d = data['data']
        recs = d.get('indexCloseOnlineRecords') if isinstance(d, dict) else d
        df = pd.DataFrame(recs)
        if df.empty:
            return None

        def find(key, avoid=()):
            for c in df.columns:
                lc = str(c).lower()
                if key in lc and not any(a in lc for a in avoid):
                    return c
            return None
        dcol = find('timestamp') or find('date')
        ccol = find('close', ('prev',))
        hcol, lcol = find('high'), find('low')
        if dcol is None or ccol is None:
            return None
        out = pd.DataFrame({'Date': pd.to_datetime(df[dcol], errors='coerce', utc=True).dt.tz_localize(None).dt.normalize(),
                            'Close': pd.to_numeric(df[ccol], errors='coerce')})
        out['High'] = pd.to_numeric(df[hcol], errors='coerce') if hcol else out['Close']
        out['Low'] = pd.to_numeric(df[lcol], errors='coerce') if lcol else out['Close']
        return out.dropna(subset=['Date', 'Close'])
    except Exception:
        return None


def nse_index_history(index_name='NIFTY 50', chunks=3):
    """Official NSE daily index closes, fetched in ~1 year pieces."""
    end = dt.datetime.now(IST).date()
    frames = []
    for i in range(chunks):
        start = end - dt.timedelta(days=360)
        base = ('?indexType=' + requests.utils.quote(index_name) + '&from=' + start.strftime('%d-%m-%Y') + '&to=' + end.strftime('%d-%m-%Y'))
        data = nse_get('https://www.nseindia.com/api/historicalOR/indicesHistory' + base)
        part = parse_nse_history(data) if data else None
        if part is None or part.empty:
            data = nse_get('https://www.nseindia.com/api/historical/indicesHistory' + base) if i == 0 else None
            part = parse_nse_history(data) if data else None
        if part is None or part.empty:
            if i == 0:
                return None
            break
        frames.append(part)
        end = start - dt.timedelta(days=1)
    if not frames:
        return None
    h = pd.concat(frames).drop_duplicates('Date').sort_values('Date').set_index('Date')
    h = h[h['Close'] > 0]
    return h if len(h) >= 250 else None


ARCHIVE = {'days': 0, 'data': {}}


def nse_close_file(d):
    """One day's official NSE index close file (all indices). Returns {name: (open, high, low, close)} or None.
    A 404 means NSE had no session that day; any other failure is retried so a day is not silently dropped."""
    url = 'https://nsearchives.nseindia.com/content/indices/ind_close_all_' + d.strftime('%d%m%Y') + '.csv'
    r = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=12)
            if r.status_code == 200 or r.status_code in (403, 404):
                break
        except Exception:
            r = None
        time.sleep(1.5 * (attempt + 1))
    try:
        if r is None or r.status_code != 200 or len(r.text) < 200:
            return None
        df = pd.read_csv(StringIO(r.text))
        df.columns = [str(c).strip().lower() for c in df.columns]

        def col(key):
            return next((c for c in df.columns if key in c), None)
        ncol, ocol, hcol, lcol, ccol = col('index name'), col('open'), col('high'), col('low'), col('clos')
        if ncol is None or ccol is None:
            return None
        names = df[ncol].astype(str).str.strip().str.lower()
        out = {}
        for nm in ('nifty 50', 'india vix'):
            row = df[names == nm]
            if not len(row):
                continue
            vals = []
            for c in (ocol, hcol, lcol, ccol):
                vals.append(pd.to_numeric(row[c].iloc[0], errors='coerce') if c else float('nan'))
            if vals[3] == vals[3] and vals[3] > 0:
                c_ = float(vals[3])
                out[nm] = tuple(float(v) if v == v and v > 0 else c_ for v in vals)
        return out or None
    except Exception:
        return None


def nse_archive(cal_days=900):
    """Daily closes for Nifty 50 and India VIX from NSE's own archive files, fetched in parallel."""
    if ARCHIVE['days'] >= cal_days and ARCHIVE['data']:
        return ARCHIVE['data']
    today = dt.datetime.now(IST).date()
    days = [today - dt.timedelta(days=i) for i in range(cal_days + 1)]
    # all seven days: NSE sometimes trades on a weekend (Diwali Muhurat session, special Saturday sessions)
    if not any(nse_close_file(d) for d in days[:4]):      # quick probe so a blocked host fails fast
        return {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(nse_close_file, days))
    rows = {'nifty 50': [], 'india vix': []}
    for d, res in zip(days, results):
        for nm in rows:
            if res and nm in res:
                o, h, l, c = res[nm]
                rows[nm].append({'Date': pd.Timestamp(d), 'Open': o, 'High': h, 'Low': l, 'Close': c})
    data = {}
    for nm, lst in rows.items():
        if lst:
            data[nm] = pd.DataFrame(lst).drop_duplicates('Date').sort_values('Date').set_index('Date')
    ARCHIVE['days'], ARCHIVE['data'] = cal_days, data
    return data


def nse_archive_nifty():
    h = nse_archive(900).get('nifty 50')
    return h if h is not None and len(h) >= 250 else None


def nse_vix():
    """(last, previous) India VIX close from NSE's archive, or None."""
    v = nse_archive(15).get('india vix')
    if v is None or len(v) < 2:
        return None
    return float(v['Close'].iloc[-1]), float(v['Close'].iloc[-2])


def yahoo_history(symbol):
    h = yf.Ticker(symbol).history(period='3y').dropna(subset=['High', 'Low', 'Close'])
    if getattr(h.index, 'tz', None) is not None:
        h.index = h.index.tz_localize(None)
    h.index = h.index.normalize()
    h = h[~h.index.duplicated(keep='last')]
    h = h[h['Close'] > 0]
    now = dt.datetime.now(IST)
    if len(h) and h.index[-1].date() == now.date() and now.hour < 16:
        h = h.iloc[:-1]      # today's candle is still forming, so use the last completed close
    return h


def get_history(symbol):
    """Daily history for an index. Nifty uses NSE's official closes (API, then NSE archive files); Sensex is a BSE index, so Yahoo."""
    h, src = None, ''
    if symbol == '^NSEI':
        h = safe(nse_index_history, 'NIFTY 50')
        src = 'NSE official index closes'
        if h is None:
            h = safe(nse_archive_nifty)
            src = 'NSE official index close files'
    if h is None:
        h = safe(yahoo_history, symbol)
        src = 'Yahoo Finance'
    if h is None or len(h) < 30:
        return None
    HIST_INFO[symbol] = (src, h.index[-1].strftime('%d %b %Y'), len(h))
    return h


def ema_last(close, p):
    """Standard EMA: smoothing 2/(p+1), seeded with the simple average of the first p closes."""
    v = [float(x) for x in close]
    if len(v) < p:
        return float('nan')
    k = 2.0 / (p + 1)
    e = sum(v[:p]) / p
    for x in v[p:]:
        e = x * k + e * (1 - k)
    return e


TRENDLYNE = {'^NSEI': 'https://trendlyne.com/equity/technical-analysis/NIFTY50/1887/nifty-50/',
             '^BSESN': 'https://trendlyne.com/equity/technical-analysis/BSESENSEX/4482/bse-sensex/'}
MA_SRC = {}


def trendlyne_ma(symbol):
    """SMA (and EMA, if the page carries it) values published on Trendlyne's technical page for the index."""
    url = TRENDLYNE.get(symbol)
    if not url:
        return None
    hdr = dict(HEADERS)
    hdr['Accept'] = 'text/html,application/xhtml+xml'
    hdr['Referer'] = 'https://trendlyne.com/'
    for _ in range(1):
        try:
            r = requests.get(url, headers=hdr, timeout=15)
            if r.status_code == 200 and len(r.text) > 5000:
                text = re.sub(r'(?is)<(script|style).*?</\1>', ' ', r.text)
                text = html.unescape(re.sub(r'<[^>]+>', ' ', text))
                vals = {}
                for per, kind, v in re.findall(r'(\d{1,3})\s*Day\s*(SMA|EMA)\s*:?\s*([\d,]+(?:\.\d+)?)', text):
                    vals.setdefault((kind, int(per)), float(v.replace(',', '')))
                if all(('SMA', q) in vals for q in PERIODS):
                    m = re.search(r'(\d{1,2} \w{3},? \d{4},? \d{1,2}:\d{2} ?[AP]M)', text)
                    return {'vals': vals, 'when': m.group(1) if m else ''}
        except Exception:
            pass
        time.sleep(3)
    return None


def ma_table(h, tl=None, sym=None):
    """Moving averages. SMA/EMA come from Trendlyne when available; otherwise from our own calculation on the closes."""
    if h is None:
        return None, None
    try:
        c = h['Close']
        last = float(c.iloc[-1])
        rows, diffs, tl_ema = [], [], False
        for p in PERIODS:
            sma = float(c.rolling(p).mean().iloc[-1])
            ema = ema_last(c, p)
            if tl:
                t = tl['vals'].get(('SMA', p))
                if t:
                    diffs.append(abs(t / sma - 1) * 100)
                    sma = t
                te = tl['vals'].get(('EMA', p))
                if te:
                    ema, tl_ema = te, True
            rows.append({'Period (days)': p, 'SMA': round(sma, 2), 'Price vs SMA (%)': round((last / sma - 1) * 100, 2),
                         'EMA': round(ema, 2), 'Price vs EMA (%)': round((last / ema - 1) * 100, 2)})
        if sym:
            MA_SRC[sym] = {'tl': bool(tl), 'ema': tl_ema, 'diff': max(diffs) if diffs else None, 'when': tl['when'] if tl else ''}
        return round(last, 2), pd.DataFrame(rows)
    except Exception:
        return None, None


def ma_analysis(name, last, df):
    if df is None or last is None:
        return []
    out = []
    valid = df.dropna(subset=['SMA'])
    above = [int(r['Period (days)']) for _, r in valid.iterrows() if r['Price vs SMA (%)'] > 0]
    if len(above) == len(valid):
        t = 'Above every average: fully bullish structure; dips to the 20 and 50 day lines are usually bought.'
    elif not above:
        t = 'Below every average: fully bearish structure; rallies to the 20 and 50 day lines tend to be sold.'
    elif 200 in above or 100 in above:
        t = 'Above the long averages but not all short ones: a pullback inside an uptrend; the 50 and 100 day lines are buyer zones.'
    else:
        t = 'Above only the short averages: a bounce inside a downtrend; the 100 and 200 day lines are resistance.'
    out.append(name + ' closed at ' + indian(last, 2) + ', above ' + str(len(above)) + ' of ' + str(len(valid)) + ' SMAs. ' + t)
    r50 = valid[valid['Period (days)'] == 50]
    if len(r50):
        d50 = float(r50['Price vs SMA (%)'].iloc[0])
        if d50 > 5:
            out.append('Stretched: ' + format(d50, '.1f') + '% above the 50-day SMA, so a pause or pullback is more likely.')
        elif d50 < -5:
            out.append('Oversold: ' + format(abs(d50), '.1f') + '% below the 50-day SMA, so a relief bounce is more likely.')
        else:
            out.append('Not stretched: ' + format(d50, '.1f') + '% from the 50-day SMA.')
    levels = []
    for _, r in df.iterrows():
        for lab in ('SMA', 'EMA'):
            if pd.notna(r[lab]):
                levels.append((lab + ' ' + str(int(r['Period (days)'])), float(r[lab])))
    below_l = sorted([x for x in levels if x[1] <= last], key=lambda x: -x[1])[:2]
    above_l = sorted([x for x in levels if x[1] > last], key=lambda x: x[1])[:2]
    bits = []
    if below_l:
        bits.append('support ' + ', '.join(a + ' ' + n(b) for a, b in below_l))
    if above_l:
        bits.append('resistance ' + ', '.join(a + ' ' + n(b) for a, b in above_l))
    if bits:
        out.append('Nearest levels: ' + '; '.join(bits) + '.')
    s50 = valid[valid['Period (days)'] == 50]['SMA']
    s200 = valid[valid['Period (days)'] == 200]['SMA']
    if len(s50) and len(s200):
        up = float(s50.iloc[0]) > float(s200.iloc[0])
        out.append('50-day SMA is ' + ('above' if up else 'below') + ' the 200-day: ' + ('golden-cross setup, long-term bullish.' if up else 'death-cross setup, long-term cautious.'))
    return out


def market_context(h):
    if h is None:
        return None
    try:
        c = h['Close']
        price, prev = float(c.iloc[-1]), float(c.iloc[-2])
        tr = pd.concat([h['High'] - h['Low'], (h['High'] - c.shift()).abs(), (h['Low'] - c.shift()).abs()], axis=1).max(axis=1)
        return {'price': price, 'chg_pct': (price / prev - 1) * 100, 'atr': float(tr.rolling(14).mean().iloc[-1]),
                'sma20': float(c.rolling(20).mean().iloc[-1]), 'sma50': float(c.rolling(50).mean().iloc[-1]),
                'sma200': float(c.rolling(200).mean().iloc[-1]),
                'hi52': float(h['High'].tail(252).max()), 'lo52': float(h['Low'].tail(252).min()),
                'ret5': (price / float(c.iloc[-6]) - 1) * 100, 'ret20': (price / float(c.iloc[-21]) - 1) * 100}
    except Exception:
        return None


# ================= DOLLAR, CRUDE, VOLATILITY =================
VIX_SRC = ['Yahoo Finance']


MONTH_CODES = 'FGHJKMNQUVXZ'


def last_business_day(y, m):
    d = dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def brent_contract(today=None):
    """Nearest Brent futures contract that has not expired. Brent expires on the last business day of the
    second month before its delivery month (the December contract expired at the end of October)."""
    today = today or dt.datetime.now(IST).date()
    y, m = today.year, today.month
    for k in range(0, 8):
        dm = (m - 1 + k) % 12 + 1
        dy = y + (m - 1 + k) // 12
        em = dm - 2
        ey = dy
        if em < 1:
            em += 12
            ey -= 1
        if last_business_day(ey, em) > today:
            return 'BZ' + MONTH_CODES[dm - 1] + str(dy)[2:] + '.NYM', dt.date(dy, dm, 1).strftime('%b %Y')
    return None, None


BRENT_NOTE = ['Brent Crude: Yahoo continuous front-month price']


def extra_table():
    rows = []
    for name, sym in EXTRA.items():
        try:
            nv = safe(nse_vix) if name == 'India VIX' else None
            if nv:
                last, prev = nv
                VIX_SRC[0] = 'NSE official close'
            else:
                h = None
                if name == 'Brent Crude':
                    csym, clab = brent_contract()
                    try:
                        hc = yf.Ticker(csym).history(period='10d')['Close'].dropna() if csym else None
                        if hc is not None and len(hc) >= 2 and hc.iloc[-1] > 0:
                            h = hc
                            BRENT_NOTE[0] = 'Brent Crude: ' + clab + ' futures, the nearest contract that has not expired'
                    except Exception:
                        h = None
                if h is None:
                    h = yf.Ticker(sym).history(period='10d')['Close'].dropna()
                last, prev = float(h.iloc[-1]), float(h.iloc[-2])
                if name == 'US 10Y Yield' and last > 20:      # some feeds quote the yield times 10
                    last, prev = last / 10, prev / 10
            rows.append({'Name': name, 'Last': round(last, 2), 'Previous close': round(prev, 2), 'Change': round(last - prev, 2), 'Change %': round((last / prev - 1) * 100, 2)})
        except Exception:
            pass
    return pd.DataFrame(rows) if rows else None


def extra_value(df, name, col='Last'):
    if df is None:
        return None
    r = df[df['Name'] == name]
    return float(r[col].iloc[0]) if len(r) else None


def extra_analysis(df):
    if df is None:
        return []
    out = []
    for _, r in df.iterrows():
        nm, c, last = r['Name'], float(r['Change %']), float(r['Last'])
        d = 'rose' if c > 0 else 'fell'
        head = nm + ' ' + d + ' ' + format(abs(c), '.2f') + '% to ' + format(last, '.2f') + '. '
        if nm == 'Dollar Index':
            out.append(head + ('Stronger dollar: usually FII selling and rupee weakness, a headwind.' if c > 0 else 'Weaker dollar: eases liquidity, favours emerging-market inflows, a tailwind.'))
        elif nm == 'US 10Y Yield':
            bps = round(float(r['Change']) * 100)
            lvl = 'high, a strong rival to emerging-market equities' if last >= 4.5 else ('elevated, keeps pressure on foreign flows' if last >= 4.0 else 'moderate, less competition for emerging-market equities')
            out.append('US 10-year yield ' + ('rose ' if c > 0 else 'fell ') + str(abs(bps)) + ' bps to ' + format(last, '.2f') + '%. Level is ' + lvl + '. '
                       + ('Rising yields pull money back to US bonds: usually FII selling, a headwind, most for IT, banks and rate-sensitive stocks.' if c > 0 else 'Falling yields push money toward emerging markets: usually FII buying, a tailwind.'))
        elif nm == 'Brent Crude':
            out.append(head + ('Higher crude widens India import bill and inflation, a headwind.' if c > 0 else 'Lower crude helps the rupee, inflation and margins, a tailwind.'))
        elif nm == 'India VIX':
            lvl = 'low, calm market, cheap protection' if last < 13 else ('normal, orderly market' if last < 18 else ('elevated, bigger swings expected, rich premiums' if last < 24 else 'high, nervous market, expensive premiums'))
            out.append(head + 'Level is ' + lvl + '. Rising VIX with a falling index means fear; falling VIX with a rising index means a comfortable uptrend.')
        elif nm == 'USD/INR':
            out.append(head + ('Weaker rupee: tends to come with FII selling; helps IT and pharma exporters.' if c > 0 else 'Firmer rupee: tends to come with foreign inflows; a tailwind for the broad market.'))
    dxy_c, y_c = extra_value(df, 'Dollar Index', 'Change %'), extra_value(df, 'US 10Y Yield', 'Change %')
    if dxy_c is not None and y_c is not None:
        if dxy_c > 0 and y_c > 0:
            out.append('Dollar and US yields are both rising: the toughest backdrop for FII flows and the rupee.')
        elif dxy_c < 0 and y_c < 0:
            out.append('Dollar and US yields are both falling: the friendliest backdrop for FII flows and the rupee.')
        else:
            out.append('Dollar and US yields are pulling in different directions: a mixed global signal, so lean on domestic flows.')
    return out


# ================= EVENT CALENDAR (next 7 days) =================
FEED_COUNTRY = {'USD': 'US', 'EUR': 'Eurozone', 'GBP': 'UK', 'JPY': 'Japan', 'CNY': 'China'}
KNOWN_HOLIDAYS = {dt.date(2026, 11, 24): 'Guru Nanak Jayanti', dt.date(2026, 12, 25): 'Christmas'}
FOMC_DECISIONS = [dt.date(2026, 12, 9)]      # last day of each meeting, US date (federalreserve.gov)
FOMC_MINUTES = [dt.date(2026, 12, 30)]
RBI_DECISIONS = [dt.date(2026, 12, 4), dt.date(2027, 2, 5)]   # RBI MPC calendar FY27
HEAVY = {'RELIANCE', 'HDFCBANK', 'ICICIBANK', 'INFY', 'TCS', 'BHARTIARTL', 'ITC', 'LT', 'SBIN', 'AXISBANK',
         'KOTAKBANK', 'HINDUNILVR', 'BAJFINANCE', 'MARUTI', 'M&M', 'SUNPHARMA', 'TATAMOTORS', 'NTPC',
         'ONGC', 'ULTRACEMCO', 'HCLTECH', 'WIPRO', 'TITAN', 'ASIANPAINT', 'POWERGRID', 'TATASTEEL', 'COALINDIA'}

WHY = [
    (('cpi', 'inflation', 'pce', 'ppi', 'wpi'), 'Inflation print; shapes rate-cut expectations, the dollar and FII flows.'),
    (('non-farm', 'employment change', 'unemployment rate', 'adp'), 'US jobs strength sets the Fed path; strong data lifts dollar and yields, a headwind for India.'),
    (('fomc', 'federal funds', 'rate statement', 'interest rate', 'rate decision', 'repo'), 'Rate decision; the biggest driver of liquidity, currencies and FII flows.'),
    (('gdp',), 'Growth read; moves bond yields and risk appetite.'),
    (('pmi', 'ism'), 'Business-activity gauge; an early read on growth.'),
    (('retail sales', 'consumer', 'confidence', 'sentiment'), 'Demand gauge; feeds growth and rate expectations.'),
    (('jobless', 'claims'), 'Weekly labour signal; feeds Fed expectations.'),
    (('crude', 'oil'), 'Oil supply signal; India imports most of its crude, so it affects the rupee and inflation.'),
    (('speaks', 'testifies', 'press conference', 'minutes'), 'Central-bank guidance; tone can move yields and the dollar.'),
    (('trade balance', 'exports', 'imports'), 'Trade and current-account signal; affects the rupee.'),
    (('iip', 'industrial production', 'core sector'), 'Factory-output signal; a read on growth.'),
    (('gst', 'auto sales', 'fiscal'), 'Demand and government-finance signal.'),
]


def why_text(name, imp):
    low = name.lower()
    for keys, txt in WHY:
        if any(k in low for k in keys):
            return txt
    return 'Can move global risk appetite.' if imp == 'High' else 'Secondary data point.'


def ev(day, time_txt, region, name, imp, why=None, fp=''):
    return {'date': day, 'time': time_txt, 'region': region, 'event': name, 'imp': imp,
            'fp': fp, 'why': why or why_text(name, imp)}


def nse_holidays():
    out = dict(KNOWN_HOLIDAYS)
    data = nse_get('https://www.nseindia.com/api/holiday-master?type=trading')
    try:
        for r in (data or {}).get('FO', []):
            d = dt.datetime.strptime(r['tradingDate'], '%d-%b-%Y').date()
            out[d] = r.get('description', 'Market holiday')
    except Exception:
        pass
    return out


def is_trading_day(d, hol):
    return d.weekday() < 5 and d not in hol


def shift_back(d, hol):
    while not is_trading_day(d, hol):
        d -= dt.timedelta(days=1)
    return d


def shift_fwd(d):
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


def last_weekday_of_month(y, m):
    d = dt.date(y, m, calendar.monthrange(y, m)[1])
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def nth_working_day(y, m, k):
    d, c = dt.date(y, m, 1), 0
    while True:
        if d.weekday() < 5:
            c += 1
            if c == k:
                return d
        d += dt.timedelta(days=1)


def expiry_events(start, end, hol):
    out = []
    for k in range(-7, (end - start).days + 8):
        day = start + dt.timedelta(days=k)
        for wd, name in ((1, 'Nifty'), (3, 'Sensex')):
            if day.weekday() != wd:
                continue
            exp = shift_back(day, hol)
            if not (start <= exp <= end):
                continue
            monthly = (day + dt.timedelta(days=7)).month != day.month
            label = name + (' monthly + weekly expiry' if monthly else ' weekly expiry')
            why = ('Expiry day: heavy option hedging, wider intraday swings and strong pull toward max pain.' if monthly
                   else 'Expiry day: option decay peaks and intraday swings widen.')
            if exp != day:
                why += ' Moved earlier because ' + day.strftime('%a %d %b') + ' is a market holiday.'
            out.append(ev(exp, 'All day', 'India', label, 'High' if monthly else 'Medium', why))
    return out


def holiday_events(start, end, hol):
    out = []
    for d, desc in hol.items():
        if start <= d <= end and d.weekday() < 5:
            out.append(ev(d, 'All day', 'India', 'NSE/BSE closed: ' + str(desc), 'High', 'No trading; expiries and settlements shift around the holiday.'))
    return out


def us_holiday_events(start, end):
    out = []
    for y in {start.year, end.year}:
        nov = [dt.date(y, 11, d) for d in range(1, 31) if dt.date(y, 11, d).weekday() == 3]
        tg = nov[3]
        out.append((tg, 'US markets closed: Thanksgiving', 'Thin global liquidity; US data and flows pause.'))
        out.append((tg + dt.timedelta(days=1), 'US markets close early (Black Friday)', 'Thin liquidity into the US close.'))
        out.append((dt.date(y, 12, 25), 'US markets closed: Christmas', 'Thin global liquidity.'))
        out.append((dt.date(y, 1, 1), "US markets closed: New Year's Day", 'Thin global liquidity.'))
    return [ev(d, 'All day', 'US', n_, 'Medium', w) for d, n_, w in out if start <= d <= end]


def india_data_events(start, end):
    out = []
    months = {(start.year, start.month), (end.year, end.month)}
    for y, m in months:
        items = [
            (shift_fwd(dt.date(y, m, 12)), 'India CPI inflation', 'High'),
            (shift_fwd(dt.date(y, m, 14)), 'India WPI inflation', 'Medium'),
            (shift_fwd(dt.date(y, m, 15)), 'India trade data (exports, imports)', 'Medium'),
            (shift_fwd(dt.date(y, m, 28)), 'India IIP industrial output', 'Medium'),
            (last_weekday_of_month(y, m), 'India fiscal deficit (CGA data)', 'Medium'),
            (nth_working_day(y, m, 1), 'India manufacturing PMI', 'Medium'),
            (nth_working_day(y, m, 1), 'India GST collections and auto sales', 'Medium'),
            (nth_working_day(y, m, 3), 'India services PMI', 'Medium'),
        ]
        if m in (2, 5, 8, 11):
            items.append((last_weekday_of_month(y, m), 'India GDP (quarterly)', 'High'))
        for d, name, imp in items:
            if start <= d <= end:
                out.append(ev(d, '17:00' if 'GDP' in name or 'CPI' in name or 'WPI' in name or 'IIP' in name else 'Varies', 'India', name + ' (usual date, confirm)', imp))
    return out


def central_bank_events(start, end):
    out = []
    try:
        ny = ZoneInfo('America/New_York')
    except Exception:
        ny = dt.timezone(dt.timedelta(hours=-5))
    for d in FOMC_DECISIONS:
        t = dt.datetime(d.year, d.month, d.day, 14, 0, tzinfo=ny).astimezone(IST)
        if start <= t.date() <= end:
            out.append(ev(t.date(), t.strftime('%H:%M'), 'US', 'US Fed FOMC rate decision and projections', 'High'))
    for d in FOMC_MINUTES:
        t = dt.datetime(d.year, d.month, d.day, 14, 0, tzinfo=ny).astimezone(IST)
        if start <= t.date() <= end:
            out.append(ev(t.date(), t.strftime('%H:%M'), 'US', 'FOMC meeting minutes', 'Medium'))
    for d in RBI_DECISIONS:
        if start <= d <= end:
            out.append(ev(d, '10:00', 'India', 'RBI monetary policy decision', 'High'))
        if start <= d - dt.timedelta(days=2) <= end:
            out.append(ev(d - dt.timedelta(days=2), 'All day', 'India', 'RBI MPC meeting begins', 'Medium', 'Policy meeting starts; the decision comes on the last day.'))
    return out


def global_feed(start, end):
    out = []
    for fn in ('thisweek', 'nextweek'):
        try:
            r = requests.get('https://nfs.faireconomy.media/ff_calendar_' + fn + '.json', headers=HEADERS, timeout=20)
            if r.status_code != 200:
                continue
            for e in r.json():
                c = FEED_COUNTRY.get(e.get('country'))
                imp = e.get('impact')
                if not c or not (imp == 'High' or imp == 'Holiday' or (imp == 'Medium' and c in ('US', 'China'))):
                    continue
                t = dt.datetime.fromisoformat(e['date']).astimezone(IST)
                if not (start <= t.date() <= end):
                    continue
                fp = ''
                if e.get('forecast') or e.get('previous'):
                    fp = 'F: ' + str(e.get('forecast') or '-') + ' / P: ' + str(e.get('previous') or '-')
                if imp == 'Holiday':
                    out.append(ev(t.date(), 'All day', c, str(e.get('title')), 'Medium', 'Market holiday; thinner global liquidity.'))
                else:
                    out.append(ev(t.date(), t.strftime('%H:%M'), c, str(e.get('title')), imp, None, fp))
        except Exception:
            continue
    return out


def corporate_events(start, end):
    out = []
    data = nse_get('https://www.nseindia.com/api/event-calendar')
    for r in data or []:
        try:
            sym = str(r.get('symbol', '')).upper()
            purpose = (str(r.get('purpose', '')) + ' ' + str(r.get('bm_desc', ''))).lower()
            if sym not in HEAVY or 'result' not in purpose:
                continue
            d = dt.datetime.strptime(str(r['date']), '%d-%b-%Y').date()
            if start <= d <= end:
                out.append(ev(d, 'Varies', 'India', sym + ' quarterly results', 'Medium', 'Heavyweight earnings can move the index and its sector.'))
        except Exception:
            continue
    return out


# ---- India-focused market-moving events
# (keywords, label, how Indian markets could react, sectors to watch). First match wins.
REACT = [
    (('expiry',), 'Two-way',
     'Option decay peaks and hedging unwinds; intraday swings widen and price tends to gravitate toward max pain', 'Nifty, Bank Nifty, Sensex, index options'),
    (('msci',), 'Two-way',
     'Index funds rebalance at the close, so passive inflows or outflows hit stocks that are added or removed; the closing auction is volatile', 'Stocks added to or dropped from MSCI India, banks, large caps'),
    (('auto sales',), 'Two-way',
     'Strong volumes (festive and post-GST-cut demand) lift autos and ancillaries; a miss hits them', 'Maruti, M&M, Tata Motors, Bajaj Auto, Hero, auto ancillaries'),
    (('india manufacturing pmi', 'india services pmi'), 'Two-way',
     'Above 50 shows expansion and supports cyclicals; a sharp fall raises growth worries', 'Capital goods, autos, banks, infra'),
    (('fiscal deficit', 'core sector', 'iip'), 'Two-way',
     'Shows how fast the economy and government finances are moving; a beat supports cyclicals and bonds, a miss weighs on them', 'Capital goods, infra, cement, banks, bond yields'),
    (('msci india', 'rbi policy decision'), 'Two-way',
     'Positioning builds before the decision: rate-cut hopes lift banks, realty and autos; a hawkish hold weighs on them', 'Bank Nifty, NBFCs, realty, autos, 10-year yield'),
    (('india cpi', 'india wpi', 'india inflation', 'retail inflation'), 'Two-way',
     'Low inflation raises rate-cut hopes (bullish); a hot print is bearish', 'Banks, NBFCs, realty, bond yields'),
    (('india gdp', 'gdp (quarterly)'), 'Two-way',
     'A beat supports earnings hopes; a miss weighs on cyclicals', 'Banks, capital goods, autos, infra'),
    (('opec', 'crude', 'brent', 'oil price', 'oil prices'), 'Two-way',
     'Output cuts or higher crude hurt India (import bill, rupee, inflation); a quota hike or lower crude helps', 'OMCs (BPCL, IOC, HPCL), paints, aviation, tyres, USD/INR'),
    (('rbi', 'repo rate', 'mpc', 'monetary policy'), 'Two-way',
     'Rate cut or easy liquidity lifts banks, realty and autos; a hawkish hold weighs on them', 'Bank Nifty, NBFCs, realty, autos, 10-year yield'),
    (('beige book', 'fed minutes'), 'Two-way',
     'Shows how the Fed reads the economy; shifts rate-cut hopes, the dollar and FII flows', 'USD/INR, IT, private banks, FII flows'),
    (('fomc', 'federal reserve', 'fed ', 'powell'), 'Two-way',
     'Dovish Fed weakens the dollar and brings FII inflows; hawkish Fed does the opposite', 'USD/INR, IT, private banks, FII flows'),
    (('cpi', 'non-farm', 'nfp', 'pce', 'employment change', 'unemployment'), 'Two-way',
     'Hot US data lifts dollar and yields (bearish for FII flows); soft data is supportive', 'Dollar index, USD/INR, IT, banks'),
    (('trade deal', 'trade talks', 'trade agreement', 'tariff', 'trade pact', 'bilateral trade'), 'Two-way',
     'A deal or tariff cut is bullish for exporters and the rupee; tariff escalation is bearish', 'IT, pharma, textiles, gems, auto parts, USD/INR'),
    (('sanction', 'war', 'ceasefire', 'missile', 'conflict', 'middle east', 'iran', 'ukraine', 'taiwan'), 'Bearish',
     'Conflict lifts oil and VIX and triggers FII selling; a ceasefire reverses it', 'Oil marketers, aviation, defence (gains), India VIX'),
    (('sebi',), 'Two-way',
     'Tighter derivatives or market rules cut volumes; relief is positive', 'Brokers, BSE, CDSL, F&O volumes'),
    (('fii', 'fpi', 'foreign investors', 'foreign portfolio'), 'Two-way',
     'Sustained FII selling pressures Nifty and the rupee; buying supports both', 'Large caps, banks, IT, USD/INR'),
    (('rupee', 'usd/inr'), 'Two-way',
     'A weak rupee helps IT and pharma exporters, hurts importers and raises inflation', 'IT, pharma, oil marketers, airlines'),
    (('budget', 'gst', 'tax'), 'Two-way', 'Policy changes hit specific sectors directly', 'Sector-specific'),
    (('pmi',), 'Two-way',
     'Global growth read: strong data supports risk appetite and exporters, weak data raises slowdown fears', 'IT, metals, autos, exporters'),
    (('ecb', 'lagarde', 'euro'), 'Two-way',
     'Indirect: moves the euro and dollar, so FII flows and IT services revenue', 'USD/INR, IT services'),
    (('china', 'asean'), 'Two-way',
     'Indirect: Asian sentiment and demand for metals and chemicals', 'Metals, chemicals, Asian peers'),
]
REACT_DEFAULT = ('Two-way', 'Indirect effect through the dollar, oil and FII flows', 'Nifty, Bank Nifty, USD/INR')

CURATED = [
    # (date, IST time, region, event, importance). Checked against published week-ahead calendars (20-21 Nov 2026).
    (dt.date(2026, 11, 23), 'Varies', 'Global', 'Global flash PMIs for November (US, euro area, UK, Japan)', 'Medium'),
    (dt.date(2026, 11, 23), 'All day', 'China', 'China-ASEAN Week opens in Chongqing (trade, investment, logistics)', 'Low'),
    (dt.date(2026, 11, 25), '19:00', 'US', 'US PCE price index (Oct), Q3 GDP second estimate and durable goods', 'High'),
    (dt.date(2026, 11, 26), '00:30', 'US', 'US Fed Beige Book (regional economy read ahead of the December meeting)', 'Medium'),
    (dt.date(2026, 11, 26), '18:00', 'Europe', 'ECB minutes of the October meeting', 'Medium'),
    (dt.date(2026, 11, 26), 'All day', 'Europe', 'Special European Council (26-27 Nov); ECB president succession on the agenda', 'Medium'),
    (dt.date(2026, 11, 30), 'After close', 'India', 'MSCI November index review takes effect after the close (passive rebalancing flows in Indian stocks)', 'High'),
    (dt.date(2026, 11, 29), 'Online', 'Global', 'OPEC+ ministers meet on 2027 production targets and quota framework', 'High'),
]


def classify(text):
    low = ' ' + text.lower() + ' '
    for keys, label, react, sectors in REACT:
        if any(k in low for k in keys):
            return label, react, sectors
    return REACT_DEFAULT


def curated_events(start, end):
    out = []
    for d, t, r, name, imp in CURATED:
        if start <= d <= end:
            out.append(ev(d, t, r, name, imp, ''))
    try:
        with open('curated_events.json', encoding='utf-8') as fh:
            for e in json.load(fh):
                d = dt.date.fromisoformat(e['date'])
                if start <= d <= end:
                    out.append(ev(d, e.get('time', 'All day'), e.get('region', 'Global'), e['event'], e.get('importance', 'Medium'), ''))
    except Exception:
        pass
    return out


def build_events():
    start = dt.datetime.now(IST).date()
    end = start + dt.timedelta(days=6)
    rows = []
    for fn in (lambda: curated_events(start, end), lambda: central_bank_events(start, end),
               lambda: [e for e in india_data_events(start, end) if e['imp'] == 'High'],
               lambda: [e for e in global_feed(start, end) if e['imp'] == 'High']):
        got = safe(fn)
        if got:
            rows.extend(got)
    seen, uniq = set(), []
    for e in rows:
        k = (e['date'], e['event'])
        if k not in seen:
            seen.add(k)
            label, react, sectors = classify(e['event'])
            e['label'], e['react'], e['sectors'] = label, react, sectors
            uniq.append(e)
    order = {'High': 0, 'Medium': 1, 'Low': 2}
    uniq.sort(key=lambda e: (e['date'], order.get(e['imp'], 3)))
    return {'start': start, 'end': end, 'events': uniq}


def effect_cls(label):
    return 'pos' if label == 'Bullish' else ('neg' if label == 'Bearish' else '')


def events_table(evs):
    rows = []
    for e in evs['events']:
        when = e['date'].strftime('%a %d %b') + (', ' + e['time'] + ' IST' if e['time'][:1].isdigit() else '')
        rows.append([(when, ''), (html.escape(e['event']), ''), (e['label'], effect_cls(e['label'])),
                     (html.escape(e['react']), ''), (html.escape(e['sectors']), ''), (e['imp'], 'hi' if e['imp'] == 'High' else '')])
    if not rows:
        return tag('p', 'No major scheduled events found for the next 7 days.', 'na')
    heads = ['When', 'Event', 'Likely effect', 'How Indian markets could react', 'Sectors / levels to watch', 'Impact']
    head = tag('tr', ''.join(tag('th', h) for h in heads))
    body = ''.join(tag('tr', ''.join(tag('td', t, c) for t, c in r)) for r in rows)
    return tag('div', tag('table', head + body, 'evt'), 'scroll')


def events_analysis(evs):
    items = evs['events']
    if not items:
        return []
    hi = [e['event'] for e in items if e['imp'] == 'High']
    out = []
    if hi:
        out.append('Highest impact: ' + '; '.join(html.escape(h) for h in hi[:3]) + '.')
    out.append('Effects show how Indian markets usually react to each type of event; the direction depends on the outcome versus expectations, so most are two-way.')
    return out


# ================= OVERALL CONCLUSION =================
def days_to_expiry(exp):
    try:
        d = dt.datetime.strptime(str(exp), '%d-%b-%Y').date()
        today = dt.datetime.now(IST).date()
        return max((d - today).days, 0), d
    except Exception:
        return None, None


def conclusion(cash, pdata, n_sum, s_sum, ctx_n, ctx_s, extra, n_label=None, events=None):
    bull, bear = [], []
    st = {'score': 0.0, 'cat': 'Flows', 'cats': {}}

    def add(points, text):
        st['score'] += points
        st['cats'][st['cat']] = st['cats'].get(st['cat'], 0) + points
        if points > 0:
            bull.append(text)
        elif points < 0:
            bear.append(text)

    fii, dii = cash_split(cash)
    if fii is not None:
        big = abs(fii) > 5000
        add((2 if big else 1) * (1 if fii > 0 else -1), 'FIIs were net ' + ('buyers' if fii > 0 else 'sellers') + ' in the cash market (Rs ' + n(abs(fii)) + ' crore)')
    if dii is not None:
        add(0.5 if dii > 0 else -0.5, 'DIIs were net ' + ('buyers' if dii > 0 else 'sellers') + ' (Rs ' + n(abs(dii)) + ' crore)')

    st['cat'] = 'Positioning'
    f = pdata.get(('Index Futures', 'FII')) if pdata else None
    sl = None
    if f:
        sl = share_long(f['lg'], f['sh'])
        if sl is not None:
            pts = 2 if sl >= 65 else (1 if sl >= 55 else (-2 if sl <= 30 else (-1 if sl <= 45 else 0)))
            if pts:
                add(pts, 'FII index futures long share is ' + format(sl, '.0f') + '%')
        if f['chg']:
            add(0.5 if f['chg'] > 0 else -0.5, 'FIIs moved their index futures net ' + ('toward long' if f['chg'] > 0 else 'toward short') + ' versus the previous day')
    fc = pdata.get(('Index Call Options', 'FII')) if pdata else None
    fp = pdata.get(('Index Put Options', 'FII')) if pdata else None
    fst = opt_stance(fc['net'], fp['net']) if fc and fp else None
    if fst == 'bullish':
        add(1, 'FIIs hold a bullish index option combination (long calls, short puts)')
    elif fst == 'bearish':
        add(-1, 'FIIs hold a bearish index option combination (long puts, short calls)')

    st['cat'] = 'Options map'
    spot = (n_sum['Spot'] if n_sum and n_sum['Spot'] else None) or (ctx_n['price'] if ctx_n else None)
    if n_sum:
        pcr = n_sum['PCR today']
        if pcr >= 1.1:
            add(1, 'Nifty PCR is ' + str(pcr) + ', put-heavy')
        elif pcr <= 0.8:
            add(-1, 'Nifty PCR is ' + str(pcr) + ', call-heavy')
        add(0.5 if n_sum['Change in Put OI'] > n_sum['Change in Call OI'] else -0.5,
            'fresh ' + ('put' if n_sum['Change in Put OI'] > n_sum['Change in Call OI'] else 'call') + ' open interest is building faster than ' + ('call' if n_sum['Change in Put OI'] > n_sum['Change in Call OI'] else 'put'))
        if spot:
            gap = (n_sum['Max pain'] - spot) / spot * 100
            if gap > 0.4:
                add(0.5, 'max pain sits above spot, a mild upward pull')
            elif gap < -0.4:
                add(-0.5, 'max pain sits below spot, a mild downward pull')
    st['cat'] = 'Trend'
    if ctx_n:
        above = sum([ctx_n['price'] > ctx_n['sma20'], ctx_n['price'] > ctx_n['sma50'], ctx_n['price'] > ctx_n['sma200']])
        if above == 3:
            add(1, 'Nifty is above its 20, 50 and 200 day averages')
        elif above == 0:
            add(-1, 'Nifty is below its 20, 50 and 200 day averages')
    st['cat'] = 'Macro'
    dxy, brent, vixc = extra_value(extra, 'Dollar Index', 'Change %'), extra_value(extra, 'Brent Crude', 'Change %'), extra_value(extra, 'India VIX', 'Change %')
    ychg = extra_value(extra, 'US 10Y Yield', 'Change')
    if ychg is not None and abs(ychg) >= 0.05:
        add(-0.5 if ychg > 0 else 0.5, 'the US 10-year yield ' + ('rose' if ychg > 0 else 'fell'))
    if dxy is not None and abs(dxy) > 0.3:
        add(-0.5 if dxy > 0 else 0.5, 'the dollar index ' + ('rose' if dxy > 0 else 'fell'))
    if brent is not None and abs(brent) > 1.5:
        add(-0.5 if brent > 0 else 0.5, 'Brent crude ' + ('rose' if brent > 0 else 'fell'))
    if vixc is not None and abs(vixc) > 5:
        add(-0.5 if vixc > 0 else 0.5, 'India VIX ' + ('jumped' if vixc > 0 else 'dropped'))

    score = st['score']
    if score >= 4:
        bias, lean = 'bullish', 'up'
    elif score >= 1.5:
        bias, lean = 'mildly bullish', 'up'
    elif score > -1.5:
        bias, lean = 'neutral to range-bound', 'flat'
    elif score > -4:
        bias, lean = 'mildly bearish', 'down'
    else:
        bias, lean = 'bearish', 'down'

    cat_lab = {}
    for k, v in st['cats'].items():
        cat_lab[k] = 'bullish' if v > 0.5 else ('bearish' if v < -0.5 else 'neutral')
    agree = sum(1 for v in cat_lab.values() if (v == 'bullish' and score > 0) or (v == 'bearish' and score < 0) or (v == 'neutral' and lean == 'flat'))
    conviction = 'high' if cat_lab and agree >= max(3, len(cat_lab) - 1) else ('moderate' if agree >= 2 else 'low (signals are mixed)')

    vix_last = extra_value(extra, 'India VIX')
    exp_str = n_sum['Nearest expiry'] if n_sum else None
    dte, exp_date = days_to_expiry(exp_str) if exp_str else (None, None)
    atr = ctx_n['atr'] if ctx_n else None
    sd = None
    method = ''
    if spot:
        if vix_last is not None and 5 < vix_last < 60:
            sd = spot * (vix_last / 100.0) * math.sqrt(max(dte if dte is not None else 1, 1) / 365.0)
            method = 'India VIX ' + format(vix_last, '.1f')
        elif atr:
            days = max(round((dte if dte is not None else 1) * 5 / 7), 1)
            sd = atr * math.sqrt(days)
            method = '14-day ATR ' + n(atr)

    rows1, rows2, strategies = [], [], []
    writer, rows3 = [], []
    quick_range = quick_idea = ''
    exp_txt = (str(exp_str) + (' (' + exp_date.strftime('%a') + ')' if exp_date else '')) if exp_str else 'the next expiry'

    ov = bias.capitalize() + ' (score ' + format(score, '+.1f') + ').'
    if cat_lab:
        ov += ' Area by area: ' + ', '.join(k.lower() + ' ' + v for k, v in cat_lab.items()) + '. Conviction is ' + conviction + '.'
    if bull and bear:
        ov += ' Signals conflict, so levels matter more than direction.'
    rows1.append(('Overall view', ov))

    if sd and n_sum:
        pw, cw, mp = n_sum['Highest Put OI strike (support)'], n_sum['Highest Call OI strike (resistance)'], n_sum['Max pain']
        tilt = sd * max(-0.5, min(0.5, score / 8.0))
        center = 0.75 * (spot + tilt) + 0.25 * mp
        lo, hi = center - 0.6 * sd, center + 0.6 * sd
        quick_range = n(lo) + ' to ' + n(hi)
        if lean == 'up':
            react = 'Dips toward ' + n(pw) + ' are likely to be bought; rallies may stall near ' + n(cw) + ', where call writers defend. Expect a steady climb more than a gap.'
        elif lean == 'down':
            react = 'Rallies toward ' + n(cw) + ' are likely to be sold; whether ' + n(pw) + ' holds decides if this is a dip or a breakdown.'
        else:
            react = 'Likely to trade between ' + n(pw) + ' and ' + n(cw) + '; a move outside needs a fresh trigger.'
        react += ' Into ' + exp_txt + ' expect a pull toward max pain ' + n(mp) + ' and sharper swings in the final session.'
        rows1.append(('Likely market reaction', react))
        rows1.append(('Nifty zone to expiry', 'Base case ' + n(lo) + ' to ' + n(hi) + ' (centre ' + n(center) + '); one-standard-deviation range ' + n(spot - sd) + ' to ' + n(spot + sd) + ' using ' + method + '.'))
        rows1.append(('If it breaks out', 'Close above ' + n(cw) + ' (with FII buying and falling VIX): about ' + n(max(spot + 1.25 * sd, cw + 0.5 * sd)) + '. Close below ' + n(pw) + ' (with FII selling and rising VIX): about ' + n(min(spot - 1.25 * sd, pw - 0.5 * sd)) + '.'))
    else:
        rows1.append(('Likely market reaction', 'Not available: option chain (live or last session) or price history could not be fetched.'))

    if events:
        ev_bits = []
        for e in sorted(events.get('events') or [], key=lambda x: {'High': 0, 'Medium': 1}.get(x['imp'], 2))[:3]:
            ev_bits.append(e['event'] + ' (' + e['date'].strftime('%a %d %b') + '): ' + e['label'].lower() + '. ' + e['react'])
        if ev_bits:
            rows1.append(('Event risk this week', ' | '.join(ev_bits)))

    # ---- strategy menu
    if sd and n_sum:
        w = max(100, rnd(sd * 0.5))
        sp = rnd(min(pw, spot - 0.8 * sd))
        sc = rnd(max(cw, spot + 0.8 * sd))
        atm = rnd(spot)
        hp, hc = rnd(spot - sd), rnd(spot + sd)
        mild = abs(score) < 4
        fit_ic = 'Strong' if lean == 'flat' else ('Moderate' if mild else 'Weak')
        fit_up = 'Strong' if lean == 'up' else ('Moderate' if lean == 'flat' else 'Weak')
        fit_dn = 'Strong' if lean == 'down' else ('Moderate' if lean == 'flat' else 'Weak')
        fit_str = 'Moderate' if (vix_last is not None and vix_last < 14) else 'Weak (premiums rich or no big trigger)'
        fit_col = 'Strong' if lean in ('flat', 'down') else 'Moderate'
        strategies = [
            ('Iron condor (neutral income)', 'Sell ' + n(sp) + ' put and ' + n(sc) + ' call; buy ' + n(sp - w) + ' put and ' + n(sc + w) + ' call', 'Nifty stays between ' + n(sp) + ' and ' + n(sc), 'Capped: ' + n(w) + ' points minus credit', fit_ic),
            ('Bull put spread (bullish income)', 'Sell ' + n(sp) + ' put; buy ' + n(sp - w) + ' put', 'Nifty stays above ' + n(sp), 'Capped: ' + n(w) + ' points minus credit', fit_up),
            ('Bear call spread (bearish income)', 'Sell ' + n(sc) + ' call; buy ' + n(sc + w) + ' call', 'Nifty stays below ' + n(sc), 'Capped: ' + n(w) + ' points minus credit', fit_dn),
            ('Bull call spread (bullish, low cost)', 'Buy ' + n(atm) + ' call; sell ' + n(max(rnd(cw), atm + w)) + ' call', 'Nifty rises toward ' + n(max(rnd(cw), atm + w)), 'Capped: debit paid', fit_up),
            ('Bear put spread (bearish, low cost)', 'Buy ' + n(atm) + ' put; sell ' + n(min(rnd(pw), atm - w)) + ' put', 'Nifty falls toward ' + n(min(rnd(pw), atm - w)), 'Capped: debit paid', fit_dn),
            ('Long straddle (big-move bet)', 'Buy ' + n(atm) + ' call and ' + n(atm) + ' put', 'Move beyond the premium paid, either way (event shock)', 'Capped: total premium; decays fast', fit_str),
            ('Collar (hedge for holders)', 'Hold the index or portfolio; buy ' + n(hp) + ' put, sell ' + n(hc) + ' call', 'Protects below ' + n(hp), 'Loss floored near ' + n(hp) + '; gains capped near ' + n(hc), fit_col),
        ]
        rank = {'Strong': 0, 'Moderate': 1}
        strategies.sort(key=lambda x: rank.get(x[4], 2))
        rank = {'Strong': 0, 'Moderate': 1, 'Weak': 2}

        if lean == 'up':
            best = 'Bull put spread: sell ' + n(sp) + ' put, buy ' + n(sp - w) + ' put, plus a collar on the core holding (buy ' + n(hp) + ' put, sell ' + n(hc) + ' call).'
            why = 'Trend, flows and positioning lean supportive, so I would be paid to buy the index lower than today with a defined loss, and the collar covers a gap-down on what I already own.'
        elif lean == 'down':
            best = 'Collar or protective put on the core holding (buy ' + n(hp) + ' put, sell ' + n(hc) + ' call), with an optional bear call spread (sell ' + n(sc) + ' call, buy ' + n(sc + w) + ' call) for income.'
            why = 'The evidence leans cautious, and an investor\'s first job is protecting capital; hedging costs little compared with a drawdown, and selling the call above the market funds it.'
        else:
            best = 'Iron condor (sell ' + n(sp) + ' put and ' + n(sc) + ' call, buy ' + n(sp - w) + ' put and ' + n(sc + w) + ' call), plus a collar on the core holding (buy ' + n(hp) + ' put, sell ' + n(hc) + ' call).'
            why = 'Signals are balanced and the option walls bracket the market, so time decay works for me inside a defined range, while the collar covers a gap in either direction.'
        rows2.append(('Best pick if I were an investor', best))
        rows2.append(('Why this one', why + ' Capped-risk structures let me size small and sleep; I would roll or close before the final afternoon of expiry.'))
        rows2.append(('Why not the others', 'Straddles need a big move and lose value daily; debit spreads need accurate timing; the rest are valid but fit today\'s view less. I would never sell options naked.'))
        rows2.append(('Sizing and exits', 'Cap total loss at 1 to 2% of capital; skip a spread paying under one-third of its width; exit on a close beyond the short strike or the matching wall; stand aside if India VIX is under about 12 or a major event is due before expiry.'))
        # ---- option-writer strategies (premium sellers)
        sp_w = rnd(min(pw, spot - 1.0 * sd))
        sc_w = rnd(max(cw, spot + 1.0 * sd))
        high_ev = [e for e in ((events or {}).get('events') or []) if e['imp'] == 'High']
        steps = ['Strong', 'Moderate', 'Weak']

        def down(fit, note_txt):
            i = min(steps.index(fit.split(' ')[0]) + 1, 2)
            return steps[i] + ' (' + note_txt + ')'

        base_ic = 'Strong' if lean == 'flat' else ('Moderate' if mild else 'Weak')
        fit_naked = base_ic
        if vix_last is not None and vix_last < 12:
            fit_naked = down(fit_naked, 'thin premiums')
        if high_ev and fit_naked.startswith('Strong'):
            fit_naked = down(fit_naked, 'major event ahead')
        fit_sd = 'Moderate' if (lean == 'flat' and vix_last is not None and vix_last >= 15) else 'Weak'
        fit_fly = 'Moderate' if lean == 'flat' else 'Weak'
        fit_cc = 'Strong' if lean == 'flat' else 'Moderate'
        writer = [
            ('Iron condor (defined risk)', 'Sell ' + n(sp_w) + ' put and ' + n(sc_w) + ' call; buy ' + n(sp_w - w) + ' put and ' + n(sc_w + w) + ' call', 'Nifty expires between ' + n(sp_w) + ' and ' + n(sc_w), 'Capped: ' + n(w) + ' points minus credit', fit_ic),
            ('Bull put spread (defined risk)', 'Sell ' + n(sp_w) + ' put; buy ' + n(sp_w - w) + ' put', 'Nifty stays above ' + n(sp_w), 'Capped: ' + n(w) + ' points minus credit', fit_up),
            ('Bear call spread (defined risk)', 'Sell ' + n(sc_w) + ' call; buy ' + n(sc_w + w) + ' call', 'Nifty stays below ' + n(sc_w), 'Capped: ' + n(w) + ' points minus credit', fit_dn),
            ('Short strangle (naked)', 'Sell ' + n(sp_w) + ' put and ' + n(sc_w) + ' call, no hedge', 'Nifty expires between ' + n(sp_w) + ' and ' + n(sc_w), 'Unlimited both sides; heavy margin', fit_naked),
            ('Short straddle (naked)', 'Sell ' + n(atm) + ' call and ' + n(atm) + ' put, no hedge', 'Nifty finishes near ' + n(atm), 'Unlimited both sides; highest premium, needs tight control', fit_sd),
            ('Iron butterfly', 'Sell ' + n(atm) + ' call and put; buy ' + n(atm + 2 * w) + ' call and ' + n(atm - 2 * w) + ' put', 'Nifty pins near ' + n(atm), 'Capped: ' + n(2 * w) + ' points minus credit', fit_fly),
            ('Jade lizard (bullish-neutral)', 'Sell ' + n(sp_w) + ' put; sell ' + n(sc_w) + ' call and buy ' + n(sc_w + w) + ' call', 'Nifty stays above ' + n(sp_w) + '; no upside risk if the credit exceeds ' + n(w) + ' points', 'Large below ' + n(sp_w) + ' (put is naked)', fit_up),
            ('Short put at support (cash-secured)', 'Sell ' + n(sp_w) + ' put', 'Nifty stays above ' + n(sp_w), 'Large if Nifty falls below ' + n(sp_w) + '; margin heavy', fit_up),
            ('Covered call (for holders)', 'Hold the index or portfolio; sell ' + n(hc) + ' call', 'Nifty stays below ' + n(hc), 'Index falls, cushioned by the premium; upside capped', fit_cc),
        ]
        writer.sort(key=lambda x: rank.get(x[4].split(' ')[0], 2))

        if lean == 'up':
            wbest = 'Bull put spread: sell ' + n(sp_w) + ' put and buy ' + n(sp_w - w) + ' put. Optional: add a far call spread (sell ' + n(rnd(max(cw, spot + 1.3 * sd))) + ' call, buy ' + n(rnd(max(cw, spot + 1.3 * sd)) + w) + ' call) to make it a bullish-skewed iron condor.'
        elif lean == 'down':
            wbest = 'Bear call spread: sell ' + n(sc_w) + ' call and buy ' + n(sc_w + w) + ' call. Optional: add a far put spread (sell ' + n(rnd(min(pw, spot - 1.3 * sd))) + ' put, buy ' + n(rnd(min(pw, spot - 1.3 * sd)) - w) + ' put) to make it a bearish-skewed iron condor.'
        else:
            wbest = 'Iron condor: sell ' + n(sp_w) + ' put and ' + n(sc_w) + ' call; buy ' + n(sp_w - w) + ' put and ' + n(sc_w + w) + ' call.'
        if vix_last is None:
            env = 'India VIX not available; treat premiums as unknown.'
        elif vix_last >= 16:
            env = 'India VIX ' + format(vix_last, '.1f') + ' is rich: selling pays well, but it is rich because risk is higher, so keep size smaller.'
        elif vix_last >= 13:
            env = 'India VIX ' + format(vix_last, '.1f') + ' is fair: normal premium for normal risk.'
        else:
            env = 'India VIX ' + format(vix_last, '.1f') + ' is thin: selling pays little for the risk, so prefer defined-risk structures, smaller size, or wait.'
        rows3.append(('Best pick if I were an option writer', wbest))
        rows3.append(('Why this one', 'Time decay earns the premium while Nifty stays inside the range; the sold strikes sit outside the one-standard-deviation zone (' + n(spot - sd) + ' to ' + n(spot + sd) + ') and behind the option walls (' + n(pw) + ' and ' + n(cw) + '), and the bought wings cap the loss on a gap. I choose defined risk because one bad gap erases many months of premium.'))
        rows3.append(('Premium environment', env))
        rows3.append(('Strike selection', 'Sell outside the one-standard-deviation range and behind the OI walls, roughly 15 to 20 delta; buy wings ' + n(w) + ' points further out.'))
        rows3.append(('Entry timing', 'Enter when India VIX is calm or falling, not on a high-impact event day' + ((' (next: ' + high_ev[0]['event'] + ', ' + high_ev[0]['date'].strftime('%a %d %b') + ')') if high_ev else '') + '. Decay is fastest in the last 2 to 3 sessions, but so is gamma risk.'))
        rows3.append(('Adjustments and exits', 'Book profit at 50 to 70% of the maximum; exit if Nifty closes beyond a short strike or the matching OI wall; roll the untested side closer only in a calm market; never average a losing short.'))
        rows3.append(('Capital and risk', 'Cap the maximum loss per trade at 1 to 2% of capital; check the broker margin calculator; keep margin use under about 30 to 40% of capital to survive a gap. Hedged structures need far less margin than naked ones.'))
        rows3.append(('Expiry-day rule', 'Short options carry the most gamma risk on expiry day; close or hedge shorts by early afternoon and do not hold naked shorts into the last hour.'))
        rows3.append(('What I would avoid', 'Naked short straddles when VIX is low, selling calls into a strong uptrend without a hedge, selling inside the one-standard-deviation range without wings, and adding to a losing short.'))

    else:
        rows2.append(('Strategies', 'Not available: option chain (live or last session) or volatility data could not be fetched.'))

    if not (sd and n_sum):
        rows3.append(('Strategies', 'Not available: option chain (live or last session) or volatility data could not be fetched.'))
    rows3.append(('Note', 'Illustrative, rule-based reading of public data; strikes ignore live premiums, margin and liquidity. Option selling can lose far more than the premium received. Not investment advice.'))

    rows2.append(('What would change the view', 'Reversal in FII cash flows or index futures, India VIX up more than about 10%, a big move in crude or the dollar, a close beyond either option wall, or a break below the 50-day average.'))
    rows2.append(('Note', 'Illustrative, rule-based reading of public data; strikes ignore live premiums, margin and liquidity. Not investment advice. Test on paper first.'))
    return rows1, rows2, strategies, bias, score, quick_range, quick_idea, writer, rows3


CSS = ('body{font-family:Segoe UI,Arial,sans-serif;background:#ffffff;color:#111111;margin:0;padding:16px;max-width:1100px;margin:auto}'
       'h1{margin:0 0 4px 0}h2{border-left:5px solid #111111;padding-left:10px;margin-top:34px}'
       'h3{color:#111111;margin:18px 0 4px 0}'
       '.scroll{overflow-x:auto}'
       'table{border-collapse:collapse;width:100%;margin:8px 0;font-size:14px;background:#ffffff}'
       'th{background:#eeeeee;color:#111111;text-align:left;padding:9px;white-space:nowrap;border-bottom:2px solid #111111}'
       'td{padding:8px;border-bottom:1px solid #dddddd;white-space:nowrap}tr:nth-child(even) td{background:#fafafa}'
       '.pos{color:#15803d;font-weight:600}.neg{color:#b91c1c;font-weight:600}'
       '.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}'
       '.card{background:#f5f5f5;border:1px solid #dddddd;border-radius:8px;padding:12px;font-size:14px}'
       '.analysis{background:#f7f7f7;border-left:4px solid #111111;border-radius:4px;padding:8px 14px;margin:8px 0 14px 0;font-size:14px}'
       '.analysis b{color:#111111}.analysis p{margin:8px 0;line-height:1.6}.analysis ul{margin:6px 0;padding-left:20px}.analysis li{margin:5px 0;line-height:1.5}'
       '.concl td{white-space:normal;line-height:1.5;vertical-align:top}.concl td:first-child{font-weight:600;width:200px}.strat td:first-child{width:180px}.strat td:nth-child(2){min-width:230px}'
       '.evt td{white-space:normal;vertical-align:top;line-height:1.4;min-width:90px}.evt td:nth-child(2){min-width:260px}.evt td:nth-child(4){min-width:260px}.evt td:nth-child(5){min-width:170px}'
       'ul.quick{margin:4px 0 10px 0;padding-left:20px}ul.quick li{margin:5px 0;line-height:1.5;font-size:14px}'
       '.hi{font-weight:700}.na{color:#b45309}.src{font-size:13px;color:#555555;margin:4px 0}')


def main():
    now = dt.datetime.now(IST).strftime('%d %b %Y, %H:%M IST')
    cash = safe(fii_dii_cash)
    files = safe(last_files, 3)
    pdata = safe(participant_data, files) if files else None
    hist = {name: safe(get_history, sym) for name, sym in INDICES}
    ctx = {name: safe(market_context, hist[name]) for name, _ in INDICES}
    ctx_n, ctx_s = ctx['Nifty 50'], ctx['Sensex']
    extra = safe(extra_table)

    # option chains: live first, then the last trading session's end-of-day file
    n_df, n_spot, n_label = safe(option_chain_nse, 'NIFTY') or (None, None, None)
    if n_df is None:
        n_df, n_spot, n_label = safe(bhavcopy_chain, 'NIFTY', 'NSE') or (None, None, None)
    s_df, s_spot, s_label = safe(option_chain_sensex) or (None, None, None)
    if s_df is None:
        s_df, s_spot, s_label = safe(bhavcopy_chain, 'SENSEX', 'BSE') or (None, None, None)
    if n_df is not None and not n_spot and ctx_n:
        n_spot = ctx_n['price']

    n_sum = n_near = s_sum = None
    try:
        if n_df is not None:
            n_sum, n_near = oi_summary(n_df, n_spot)
    except Exception:
        n_sum = n_near = None
    try:
        if s_df is not None:
            s_sum, _ = oi_summary(s_df, ctx_s['price'] if ctx_s else s_spot)
    except Exception:
        s_sum = None

    page = LT + 'html' + GT + LT + 'head' + GT + LT + 'meta charset=' + Q + 'utf-8' + Q + GT
    page += LT + 'meta name=' + Q + 'viewport' + Q + ' content=' + Q + 'width=device-width,initial-scale=1' + Q + GT
    page += tag('title', 'Morning Market Dashboard') + tag('style', CSS) + LT + '/head' + GT + LT + 'body' + GT
    page += tag('h1', 'Morning Market Dashboard') + tag('p', 'Updated ' + now)
    page += tag('h2', 'How to read this page')
    page += LT + 'div class=' + Q + 'grid' + Q + GT + ''.join(tag('div', tag('b', t) + LT + 'br' + GT + d, 'card') for t, d in LEGEND) + LT + '/div' + GT

    page += tag('h2', 'FII and DII cash market flows') + table_html(cash, ('Net (Rs cr)',)) + note(safe(cash_analysis, cash) or [])

    if files and pdata:
        title = 'Derivatives positions by participant (' + files[0][0] + (', compared with ' + files[1][0] if len(files) > 1 else '') + ')'
        page += tag('h2', title)
        page += tag('p', 'Green is bullish, red is bearish (reversed for puts: buying puts is bearish). Numbers are contracts.', 'src')
        summ = safe(participant_summary_table, pdata)
        if summ:
            page += tag('h3', 'Net contracts bought (+) or sold (-) today, all segments') + summ
        for seg, _, _ in GROUPS:
            t = safe(participant_segment_table, pdata, seg, files)
            if t:
                page += tag('h3', seg) + t
        page += note(safe(participant_analysis, pdata, files) or [])
    else:
        page += tag('h2', 'Derivatives positions by participant') + tag('p', 'NSE participant file could not be fetched.', 'na')

    page += tag('h2', 'Moving averages (SMA and EMA)')
    for name, sym in INDICES:
        tl = safe(trendlyne_ma, sym)
        last, df = ma_table(hist[name], tl, sym)
        info = HIST_INFO.get(sym)
        src = MA_SRC.get(sym) or {}
        cap = ''
        if info:
            if src.get('tl'):
                cap = ('SMA from Trendlyne' + (' (updated ' + src['when'] + ')' if src.get('when') else '') + '; EMA '
                       + ('from Trendlyne' if src.get('ema') else 'calculated from ' + info[0] + ' closes')
                       + '. Price is the last close, ' + info[1] + '.')
                if src.get('diff') is not None:
                    cap += ' Our own SMA from ' + info[0] + ' differs from Trendlyne by up to ' + format(src['diff'], '.2f') + '%.'
            else:
                cap = ('Trendlyne could not be reached from the build server, so SMA and EMA are calculated from closing prices up to ' + info[1]
                       + ' (' + str(info[2]) + ' sessions, source: ' + info[0] + ').')
            cap = tag('p', cap + ' Charts during market hours include today\'s live price, so they differ until the close.', 'src')
        page += tag('h3', name) + cap + table_html(df, ('Price vs SMA (%)', 'Price vs EMA (%)')) + note(safe(ma_analysis, name, last, df) or [])

    page += tag('h2', 'Dollar, crude and volatility') + table_html(extra, ('Change', 'Change %')) + tag('p', 'India VIX: ' + VIX_SRC[0] + '. Dollar Index, US 10-year yield, Brent and USD/INR: Yahoo Finance (not traded on NSE). ' + BRENT_NOTE[0] + '. Previous close is the session before the latest one.', 'src') + note(safe(extra_analysis, extra) or [])

    evs = safe(build_events)
    if evs:
        page += tag('h2', 'Market-moving events for India: next 7 days (' + evs['start'].strftime('%d %b') + ' to ' + evs['end'].strftime('%d %b') + ')')
        page += tag('p', 'Major scheduled events and announcements that can move Nifty and Sensex. Times are IST. Green means a bullish lean, red bearish, plain means two-way (the outcome decides).', 'src')
        page += events_table(evs) + note(safe(events_analysis, evs) or [])
    else:
        page += tag('h2', 'Market-moving events for India') + tag('p', 'Events could not be built this morning.', 'na')

    page += tag('h2', 'Overall conclusion and next-expiry view')
    res = safe(conclusion, cash, pdata, n_sum, s_sum, ctx_n, ctx_s, extra, n_label, evs)
    if res:
        rows1, rows2, strategies, writer, rows3 = res[0], res[1], res[2], res[7], res[8]

        def two_col(rows):
            head = tag('tr', tag('th', 'Heading') + tag('th', 'Conclusion'))
            body = ''.join(tag('tr', tag('td', h) + tag('td', t)) for h, t in rows)
            return tag('div', tag('table', head + body, 'concl'), 'scroll')
        page += two_col(rows1)
        if strategies:
            page += tag('h3', 'Derivative strategies on Nifty for the next expiry (illustrative, best fit first)')
            head = tag('tr', ''.join(tag('th', h) for h in ['Strategy', 'Legs', 'Profits when', 'Maximum loss', 'Fit with today\'s view']))
            body = ''
            for nm, legs, works, risk, fit in strategies:
                cls = 'pos' if fit.startswith('Strong') else ('neg' if fit.startswith('Weak') else '')
                body += tag('tr', tag('td', nm) + tag('td', legs) + tag('td', works) + tag('td', risk) + tag('td', fit, cls))
            page += tag('div', tag('table', head + body, 'concl strat'), 'scroll')
        page += tag('h3', 'Best strategy and risk as an investor')
        page += two_col(rows2)
        if writer:
            page += tag('h3', 'Option-writer strategies on Nifty for the next expiry (illustrative, best fit first)')
            head = tag('tr', ''.join(tag('th', h) for h in ['Strategy', 'Legs', 'Profits when', 'Risk', 'Fit with today\'s view']))
            body = ''
            for nm, legs, works, risk, fit in writer:
                cls = 'pos' if fit.startswith('Strong') else ('neg' if fit.startswith('Weak') else '')
                body += tag('tr', tag('td', nm) + tag('td', legs) + tag('td', works) + tag('td', risk) + tag('td', fit, cls))
            page += tag('div', tag('table', head + body, 'concl strat'), 'scroll')
        page += tag('h3', 'Best strategy and risk as an option writer')
        page += two_col(rows3)
    else:
        page += tag('p', 'Conclusion could not be built this morning.', 'na')

    page += tag('p', 'Data from NSE, BSE and Yahoo Finance public pages. Figures may be delayed or missing. For study only, not trading advice.', 'na')
    page += LT + '/body' + GT + LT + '/html' + GT
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(page)
    print('index.html written at', now)


if __name__ == '__main__':
    main()
