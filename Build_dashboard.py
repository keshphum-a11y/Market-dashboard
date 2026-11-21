import calendar
import collections
import datetime as dt
import email.utils
import html
import io
import json
import math
import time
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
EXTRA = {'Dollar Index': 'DX-Y.NYB', 'Brent Crude': 'BZ=F', 'India VIX': '^INDIAVIX', 'USD/INR': 'INR=X'}
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
def get_history(symbol):
    try:
        h = yf.Ticker(symbol).history(period='2y').dropna(subset=['High', 'Low', 'Close'])
        return h if len(h) >= 30 else None
    except Exception:
        return None


def ma_table(h):
    if h is None:
        return None, None
    try:
        c = h['Close']
        last = float(c.iloc[-1])
        rows = []
        for p in PERIODS:
            sma = float(c.rolling(p).mean().iloc[-1])
            ema = float(c.ewm(span=p, adjust=False).mean().iloc[-1])
            rows.append({'Period (days)': p, 'SMA': round(sma, 2), 'Price vs SMA (%)': round((last / sma - 1) * 100, 2),
                         'EMA': round(ema, 2), 'Price vs EMA (%)': round((last / ema - 1) * 100, 2)})
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
        elif nm == 'Brent Crude':
            out.append(head + ('Higher crude widens India import bill and inflation, a headwind.' if c > 0 else 'Lower crude helps the rupee, inflation and margins, a tailwind.'))
        elif nm == 'India VIX':
            lvl = 'low, calm market, cheap protection' if last < 13 else ('normal, orderly market' if last < 18 else ('elevated, bigger swings expected, rich premiums' if last < 24 else 'high, nervous market, expensive premiums'))
            out.append(head + 'Level is ' + lvl + '. Rising VIX with a falling index means fear; falling VIX with a rising index means a comfortable uptrend.')
        elif nm == 'USD/INR':
            out.append(head + ('Weaker rupee: tends to come with FII selling; helps IT and pharma exporters.' if c > 0 else 'Firmer rupee: tends to come with foreign inflows; a tailwind for the broad market.'))
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
            (last_weekday_of_month(y, m), 'India fiscal deficit (CGA data)', 'Low'),
            (nth_working_day(y, m, 1), 'India manufacturing PMI', 'Medium'),
            (nth_working_day(y, m, 1), 'India GST collections and auto sales', 'Low'),
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


# ---- market-moving news and announcements (automatic, from public news headlines)
NEWS_THEMES = [
    ('Trade deals and tariffs', '(trade deal OR tariffs OR "trade talks" OR "trade agreement") (India OR US OR China OR Trump)',
     'Tariff and trade-deal news moves export sectors (IT, pharma, textiles, autos), the rupee and FII flows.'),
    ('War, sanctions and geopolitics', '(war OR ceasefire OR sanctions OR "Middle East" OR Russia OR Ukraine OR Iran OR Taiwan) markets',
     'Conflict raises oil and safe-haven demand, lifts volatility and usually triggers FII selling in emerging markets.'),
    ('Oil, OPEC and commodities', '(OPEC OR "crude oil" OR Brent OR "oil prices")',
     'India imports most of its crude; higher oil hurts the rupee, inflation and oil-marketing, paint and aviation stocks.'),
    ('Central banks and rates', '(Fed OR RBI OR ECB OR "rate cut" OR "rate hike" OR "interest rates")',
     'Rate expectations drive the dollar, bond yields and foreign flows into Indian equities.'),
    ('India policy and economy', '(India AND (SEBI OR RBI OR "Union Budget" OR GST OR "Finance Ministry" OR "Sensex" OR "Nifty"))',
     'Domestic policy and regulatory moves hit sectors directly (banks, NBFCs, derivatives rules, taxes).'),
    ('US politics and policy', '(Trump OR "White House" OR Congress OR "government shutdown") economy',
     'US policy shifts move the dollar, yields and global risk appetite.'),
]
UPCOMING_WORDS = ('next week', 'this week', 'will meet', 'to meet', 'set to', 'scheduled', 'deadline', 'talks', 'summit',
                  'expected to', 'due ', 'ahead of', 'to announce', 'will announce', 'visit', 'meeting', 'vote')

CURATED = [
    # (date, time, region, event, importance, why) - facts checked against published week-ahead calendars
    (dt.date(2026, 11, 23), 'All day', 'China', 'China-ASEAN Week opens in Chongqing (trade, investment, logistics)', 'Medium',
     'Regional trade and supply-chain headlines; sentiment for Asian markets.'),
    (dt.date(2026, 11, 26), 'All day', 'Europe', 'Eurozone leaders meet in Brussels (26-27 Nov) on a successor to ECB President Lagarde', 'Medium',
     'Leadership at the ECB affects euro and global rate expectations.'),
    (dt.date(2026, 11, 29), 'Online', 'Global', 'OPEC+ ministers meet on 2027 production targets and quota framework', 'High',
     'Oil-supply decision; India imports most of its crude, so it affects the rupee, inflation and oil-linked stocks.'),
]


def curated_events(start, end):
    out = []
    for d, t, r, name, imp, why in CURATED:
        if start <= d <= end:
            out.append(ev(d, t, r, name, imp, why))
    try:
        with open('curated_events.json', encoding='utf-8') as fh:
            for e in json.load(fh):
                d = dt.date.fromisoformat(e['date'])
                if start <= d <= end:
                    out.append(ev(d, e.get('time', 'All day'), e.get('region', 'Global'), e['event'], e.get('importance', 'Medium'), e.get('why')))
    except Exception:
        pass
    return out


def fetch_news(query):
    url = 'https://news.google.com/rss/search?q=' + requests.utils.quote(query + ' when:2d') + '&hl=en-IN&gl=IN&ceid=IN:en'
    r = requests.get(url, headers=HEADERS, timeout=20)
    if r.status_code != 200:
        return []
    root = ET.fromstring(r.content)
    now_utc = dt.datetime.now(dt.timezone.utc)
    out, seen = [], set()
    for it in root.iter('item'):
        try:
            title = (it.findtext('title') or '').strip()
            src = (it.findtext('source') or '').strip()
            if src and title.endswith(' - ' + src):
                title = title[:-(len(src) + 3)]
            key = title.lower()[:45]
            if not title or key in seen:
                continue
            seen.add(key)
            pub = email.utils.parsedate_to_datetime(it.findtext('pubDate'))
            age = (now_utc - pub).total_seconds() / 3600.0
            if age < -1 or age > 50:
                continue
            low = title.lower()
            out.append({'title': title, 'link': it.findtext('link') or '', 'src': src, 'age': max(age, 0),
                        'upcoming': any(w in low for w in UPCOMING_WORDS)})
        except Exception:
            continue
    return out


def news_by_theme(per_theme=4):
    res = []
    for name, query, why in NEWS_THEMES:
        items = safe(fetch_news, query) or []
        items = sorted(enumerate(items), key=lambda x: (not x[1]['upcoming'], x[0]))
        res.append({'theme': name, 'why': why, 'items': [i for _, i in items[:per_theme]]})
    return res


def age_text(h):
    return (str(int(h)) + 'h ago') if h < 24 else (str(int(h // 24)) + 'd ago')


def news_table(news):
    rows = []
    for blk in news:
        for i, it in enumerate(blk['items']):
            link = LT + 'a href=' + Q + html.escape(it['link'], quote=True) + Q + ' target=' + Q + '_blank' + Q + ' rel=' + Q + 'noopener' + Q + GT + html.escape(it['title']) + LT + '/a' + GT
            rows.append([(blk['theme'] if i == 0 else '', 'hi'), (link, ''), (html.escape(it['src']), ''), (age_text(it['age']), ''),
                         ('Upcoming' if it['upcoming'] else 'Developing', 'hi' if it['upcoming'] else ''),
                         (blk['why'] if i == 0 else '', '')])
    if not rows:
        return tag('p', 'Live headlines could not be fetched this morning.', 'na')
    head = tag('tr', ''.join(tag('th', h) for h in ['Theme', 'Headline', 'Source', 'Age', 'Type', 'Why markets care']))
    body = ''.join(tag('tr', ''.join(tag('td', t, c) for t, c in r)) for r in rows)
    return tag('div', tag('table', head + body, 'evt news'), 'scroll')


def news_analysis(news):
    out = []
    for blk in news:
        if blk['items']:
            top = blk['items'][0]
            out.append(blk['theme'] + ': ' + str(len(blk['items'])) + (' live story' if len(blk['items']) == 1 else ' live stories') + '; lead story: ' + html.escape(top['title']) + ' (' + html.escape(top['src']) + ', ' + age_text(top['age']) + ').')
    up = [i for b in news for i in b['items'] if i['upcoming']]
    if up:
        out.append(str(len(up)) + ' stories point to something still to happen (talks, meetings, deadlines, announcements); these are the ones to watch for a market reaction.')
    if out:
        out.append('Headlines are collected automatically and ranked by keywords, so read them as a radar, not a verdict. Open the story before acting on it.')
    return out


def build_events():
    start = dt.datetime.now(IST).date()
    end = start + dt.timedelta(days=6)
    hol = nse_holidays()
    rows = []
    for fn in (lambda: expiry_events(start, end, hol), lambda: holiday_events(start, end, hol),
               lambda: us_holiday_events(start, end), lambda: india_data_events(start, end),
               lambda: central_bank_events(start, end), lambda: global_feed(start, end),
               lambda: corporate_events(start, end), lambda: curated_events(start, end)):
        got = safe(fn)
        if got:
            rows.extend(got)
    seen, uniq = set(), []
    for e in rows:
        k = (e['date'], e['event'])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    order = {'High': 0, 'Medium': 1, 'Low': 2}
    uniq.sort(key=lambda e: (e['date'], '00:00' if e['time'] in ('All day', 'Varies') else e['time'], order.get(e['imp'], 3)))
    return {'start': start, 'end': end, 'events': uniq, 'news': safe(news_by_theme) or []}


def events_table(evs):
    rows = []
    for e in evs['events']:
        rows.append([(e['date'].strftime('%a %d %b'), ''), (e['time'], ''), (e['region'], ''), (e['event'], ''),
                     (e['imp'], 'hi' if e['imp'] == 'High' else ''), (e['why'], '')])
    if not rows:
        return tag('p', 'No scheduled catalysts found for the next 7 days.', 'na')
    head = tag('tr', ''.join(tag('th', h) for h in ['Date (IST)', 'Time (IST)', 'Region', 'Event', 'Importance', 'Why it matters']))
    body = ''.join(tag('tr', ''.join(tag('td', t, c) for t, c in r)) for r in rows)
    return tag('div', tag('table', head + body, 'evt'), 'scroll')


def events_analysis(evs):
    out = []
    items = evs['events']
    high = [e for e in items if e['imp'] == 'High']
    if high:
        out.append('High-importance: ' + '; '.join(e['event'] + ' (' + e['date'].strftime('%a %d %b') + ')' for e in high[:6]) + '.')
    exp = [e for e in items if 'expiry' in e['event']]
    if exp:
        out.append('Derivatives expiry: ' + '; '.join(e['event'] + ' on ' + e['date'].strftime('%a %d %b') for e in exp) + '. Expect wider intraday swings and strikes acting as magnets.')
    hol = [e for e in items if 'closed' in e['event'].lower() and e['region'] == 'India']
    if hol:
        out.append('Indian market holiday: ' + '; '.join(e['event'].replace('NSE/BSE closed: ', '') + ' on ' + e['date'].strftime('%a %d %b') for e in hol) + '. Expiries move to the previous trading day.')
    ush = [e for e in items if e['region'] == 'US' and 'closed' in e['event'].lower()]
    if ush:
        out.append('US market holiday: ' + '; '.join(e['event'].replace('US markets closed: ', '') + ' on ' + e['date'].strftime('%a %d %b') for e in ush) + '. Global liquidity is thinner, so moves can be erratic.')
    cnt = collections.Counter(e['date'] for e in high)
    if cnt:
        d, c = cnt.most_common(1)[0]
        out.append('Busiest day: ' + d.strftime('%a %d %b') + ' with ' + str(c) + ' high-importance event' + ('s' if c > 1 else '') + '.')
    out.append('How to use: markets react to the surprise versus forecast, not the number itself. US data lands after the Indian close, so it shows up at the next open.')
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
    st = {'score': 0.0}

    def add(points, text):
        st['score'] += points
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
    if ctx_n:
        above = sum([ctx_n['price'] > ctx_n['sma20'], ctx_n['price'] > ctx_n['sma50'], ctx_n['price'] > ctx_n['sma200']])
        if above == 3:
            add(1, 'Nifty is above its 20, 50 and 200 day averages')
        elif above == 0:
            add(-1, 'Nifty is below its 20, 50 and 200 day averages')
    dxy, brent, vixc = extra_value(extra, 'Dollar Index', 'Change %'), extra_value(extra, 'Brent Crude', 'Change %'), extra_value(extra, 'India VIX', 'Change %')
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

    rows = []
    vix_last = extra_value(extra, 'India VIX')
    exp_str = n_sum['Nearest expiry'] if n_sum else None
    dte, exp_date = days_to_expiry(exp_str) if exp_str else (None, None)

    rows.append(('Overall bias', bias.capitalize() + ' (score ' + format(score, '+.1f') + '; +4 or more bullish, -4 or less bearish, in between neutral to mild).'))
    rows.append(('Supportive factors', '; '.join(bull) + '.' if bull else 'None.'))
    rows.append(('Cautionary factors', '; '.join(bear) + '.' if bear else 'None.'))

    if fii is not None or dii is not None:
        t = []
        if fii is not None:
            t.append('FIIs net ' + ('bought' if fii > 0 else 'sold') + ' Rs ' + n(abs(fii)) + ' crore')
        if dii is not None:
            t.append('DIIs net ' + ('bought' if dii > 0 else 'sold') + ' Rs ' + n(abs(dii)) + ' crore')
        rows.append(('Cash flows', ', '.join(t) + '.'))

    if f and sl is not None:
        t = 'FII index futures ' + format(sl, '.0f') + '% long (net ' + sgn(f['net']) + ')'
        if fst:
            t += '; FII options ' + STANCE_TEXT.get(fst, '')
        ci = pdata.get(('Index Futures', 'Client'))
        if ci:
            t += '; Clients net ' + sgn(ci['net']) + (', opposite to FIIs' if f['net'] * ci['net'] < 0 else ', same side as FIIs')
        rows.append(('Derivatives positioning', t + '.'))

    if n_sum and spot:
        pw, cw, mp = n_sum['Highest Put OI strike (support)'], n_sum['Highest Call OI strike (resistance)'], n_sum['Max pain']
        t = 'Expiry ' + str(exp_str)
        if exp_date is not None:
            t += ' (' + exp_date.strftime('%A') + (', ' + str(dte) + ' day' + ('' if dte == 1 else 's') + ' away' if dte else ', today') + ')'
        t += '. Spot ' + indian(spot, 2) + ', support (put wall) ' + n(pw) + ', resistance (call wall) ' + n(cw) + ', max pain ' + n(mp) + ', PCR ' + str(n_sum['PCR today']) + '.'
        if n_label and 'Last trading' in str(n_label):
            t += ' Data is from the ' + str(n_label) + '.'
        rows.append(('Nifty expiry map', t))
    if s_sum:
        rows.append(('Sensex open interest', 'PCR ' + str(s_sum['PCR today']) + ', support ' + n(s_sum['Highest Put OI strike (support)']) + ', resistance ' + n(s_sum['Highest Call OI strike (resistance)']) + ', max pain ' + n(s_sum['Max pain']) + '.'))

    tr = []
    for nm, c in [('Nifty', ctx_n), ('Sensex', ctx_s)]:
        if c:
            pos = 'above the 50 and 200 day averages (uptrend)' if c['price'] > c['sma50'] and c['price'] > c['sma200'] else ('below the 50-day but above the 200-day (correction inside an uptrend)' if c['price'] > c['sma200'] else 'below the 200-day average (weak structure)')
            tr.append(nm + ' ' + n(c['price']) + ' (' + format(c['chg_pct'], '+.2f') + '% today, ' + format(c['ret20'], '+.1f') + '% in 20 sessions), ' + pos)
    if tr:
        rows.append(('Trend', '; '.join(tr) + '.'))

    mac = []
    if dxy is not None:
        mac.append('Dollar index ' + format(dxy, '+.2f') + '% (' + ('headwind' if dxy > 0 else 'tailwind') + ')')
    if brent is not None:
        mac.append('Brent ' + format(brent, '+.2f') + '% (' + ('headwind' if brent > 0 else 'tailwind') + ')')
    if vix_last is not None:
        mac.append('India VIX ' + format(vix_last, '.1f'))
    if mac:
        rows.append(('Macro', ', '.join(mac) + '.'))

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
    quick_range = quick_idea = ''
    if sd and n_sum:
        pw, cw, mp = n_sum['Highest Put OI strike (support)'], n_sum['Highest Call OI strike (resistance)'], n_sum['Max pain']
        tilt = sd * max(-0.5, min(0.5, score / 8.0))
        center = 0.75 * (spot + tilt) + 0.25 * mp
        lo, hi = center - 0.6 * sd, center + 0.6 * sd
        quick_range = n(lo) + ' to ' + n(hi)
        rows.append(('Nifty range at next expiry', 'Base case ' + n(lo) + ' to ' + n(hi) + ', centred near ' + n(center) + ' (' + ('mild upward' if lean == 'up' else ('mild downward' if lean == 'down' else 'neutral')) + ' tilt). One-standard-deviation range from ' + method + ': ' + n(spot - sd) + ' to ' + n(spot + sd) + '. Option walls: ' + n(pw) + ' to ' + n(cw) + '.'))
        rows.append(('Bullish scenario', 'Close above ' + n(cw) + ' with FII buying and falling VIX; target about ' + n(max(spot + 1.25 * sd, cw + 0.5 * sd)) + '.'))
        rows.append(('Bearish scenario', 'Close below ' + n(pw) + ' with FII selling and rising VIX; target about ' + n(min(spot - 1.25 * sd, pw - 0.5 * sd)) + '.'))
        w = max(100, rnd(sd * 0.5))
        sp = rnd(min(pw, spot - 0.8 * sd))
        sc = rnd(max(cw, spot + 0.8 * sd))
        atm = rnd(spot)
        if lean == 'flat':
            quick_idea = 'iron condor'
            st_txt = 'Iron condor: sell ' + n(sp) + ' put and ' + n(sc) + ' call, buy ' + n(sp - w) + ' put and ' + n(sc + w) + ' call. Profits if Nifty stays between ' + n(sp) + ' and ' + n(sc) + '; max loss is wing width (' + n(w) + ' points) minus credit.'
        elif lean == 'up':
            quick_idea = 'bull put spread'
            st_txt = 'Bull put spread: sell ' + n(sp) + ' put, buy ' + n(sp - w) + ' put. Profits if Nifty stays above ' + n(sp) + '. Directional alternative: bull call spread, buy ' + n(atm) + ' call, sell ' + n(max(rnd(cw), atm + w)) + ' call.'
        else:
            quick_idea = 'bear call spread'
            st_txt = 'Bear call spread: sell ' + n(sc) + ' call, buy ' + n(sc + w) + ' call. Profits if Nifty stays below ' + n(sc) + '. Directional alternative: bear put spread, buy ' + n(atm) + ' put, sell ' + n(min(rnd(pw), atm - w)) + ' put.'
        rows.append(('Ideal strategy (illustrative)', st_txt + ' Strikes come from the OI walls and implied range; premiums, margin and liquidity are not considered.'))
        rows.append(('Risk management', 'Cap total loss at 1 to 2% of capital; skip a spread paying under one-third of its width; exit on a close beyond the short strike or the matching wall; close short options before the last afternoon; no naked shorts around events.'))
        rows.append(('Stand aside if', 'India VIX under about 12, a major scheduled event before expiry, or the range already broken before entry.'))
    else:
        rows.append(('Nifty range and strategy', 'Not available: option chain (live or last session) or price history could not be fetched.'))

    if events and events.get('events'):
        hi = [e for e in events['events'] if e['imp'] == 'High'][:5]
        if hi:
            rows.append(('Events to watch', '; '.join(e['event'] + ' (' + e['date'].strftime('%a %d %b') + ')' for e in hi) + '.'))
    if events and events.get('news'):
        lead = [b['theme'] + ': ' + html.escape(b['items'][0]['title']) for b in events['news'][:2] if b['items']]
        if lead:
            rows.append(('News to watch', '; '.join(lead) + '.'))
    rows.append(('What would change the view', 'Reversal in FII cash flows or index futures, India VIX up more than about 10%, a large move in crude or the dollar, a close beyond either option wall, or a break below the 50-day average.'))
    rows.append(('Note', 'Rule-based reading of public data, not a prediction or investment advice. Test ideas on paper first.'))
    return rows, bias, score, quick_range, quick_idea


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
       '.concl td{white-space:normal;line-height:1.5;vertical-align:top}.concl td:first-child{font-weight:600;width:200px}'
       '.evt td{white-space:normal;vertical-align:top;line-height:1.4;min-width:90px}.evt td:nth-child(4){min-width:200px}.evt td:nth-child(6){min-width:240px}.news td:nth-child(2){min-width:320px}'
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

    exp = n_sum['Nearest expiry'] if n_sum else 'unavailable'
    page += tag('h2', 'Open interest: Nifty 50 vs Sensex (nearest expiry ' + str(exp) + ')')
    if n_sum:
        srcs = 'Data source. Nifty: ' + str(n_label) + '.'
        if s_sum:
            srcs += ' Sensex: ' + str(s_label) + '.'
        page += tag('p', srcs, 'src')
        if 'Last trading' in str(n_label) or 'Last trading' in str(s_label):
            page += tag('p', 'The live option chain was not available, so the figures marked as last trading session are the closing open interest of that day.', 'na')
        page += table_html(oi_compare(n_sum, s_sum))
        if s_sum is None:
            page += tag('p', 'Sensex option data could not be fetched from BSE, either live or from the last trading session.', 'na')
        page += note((safe(oi_analysis, 'Nifty', n_sum, ctx_n['chg_pct'] if ctx_n else None) or []) +
                     (safe(oi_analysis, 'Sensex', s_sum, ctx_s['chg_pct'] if ctx_s else None) or []))
    else:
        page += tag('p', 'NSE option chain could not be fetched, either live or from the last trading session.', 'na')

    page += tag('h2', 'Moving averages (SMA and EMA)')
    for name, _ in INDICES:
        last, df = ma_table(hist[name])
        page += tag('h3', name) + table_html(df, ('Price vs SMA (%)', 'Price vs EMA (%)')) + note(safe(ma_analysis, name, last, df) or [])

    page += tag('h2', 'Dollar, crude and volatility') + table_html(extra, ('Change', 'Change %')) + note(safe(extra_analysis, extra) or [])

    evs = safe(build_events)
    if evs:
        page += tag('h2', 'Market-moving events: next 7 days (' + evs['start'].strftime('%d %b') + ' to ' + evs['end'].strftime('%d %b') + ')')
        page += tag('h3', 'Live news and announcements that can move markets (last 48 hours)')
        page += tag('p', 'Collected automatically from public news headlines at each refresh. Click a headline to read the story.', 'src')
        if evs.get('news'):
            page += news_table(evs['news']) + note(safe(news_analysis, evs['news']) or [])
        else:
            page += tag('p', 'Live headlines could not be fetched this morning.', 'na')
        page += tag('h3', 'Scheduled catalysts (times in IST)')
        page += tag('p', 'Expiries, holidays, central-bank dates and major meetings. RBI and Fed dates are from official schedules; India data dates follow the usual release pattern, so confirm timing before trading.', 'src')
        page += events_table(evs) + note(safe(events_analysis, evs) or [])
    else:
        page += tag('h2', 'Market-moving events: next 7 days') + tag('p', 'Events could not be built this morning.', 'na')

    page += tag('h2', 'Overall conclusion and next-expiry view')
    res = safe(conclusion, cash, pdata, n_sum, s_sum, ctx_n, ctx_s, extra, n_label, evs)
    if res:
        rows = res[0]
        head = tag('tr', tag('th', 'Heading') + tag('th', 'Conclusion'))
        body = ''.join(tag('tr', tag('td', h) + tag('td', t)) for h, t in rows)
        page += tag('div', tag('table', head + body, 'concl'), 'scroll')
    else:
        page += tag('p', 'Conclusion could not be built this morning.', 'na')

    page += tag('p', 'Data from NSE, BSE and Yahoo Finance public pages. Figures may be delayed or missing. For study only, not trading advice.', 'na')
    page += LT + '/body' + GT + LT + '/html' + GT
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(page)
    print('index.html written at', now)


if __name__ == '__main__':
    main()
