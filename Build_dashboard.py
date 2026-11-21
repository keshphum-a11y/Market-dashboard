import datetime as dt
import math
import time
from io import StringIO

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


def n(v):
    try:
        return format(int(round(float(v))), ',')
    except Exception:
        return str(v)


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
            return format(v, ',.2f')
        return format(int(round(v)), ',')
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


def note(paras, title='Analysis'):
    paras = [p for p in paras if p]
    if not paras:
        return ''
    return tag('div', tag('b', title) + ''.join(tag('p', p) for p in paras), 'analysis')


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
        if fii < 0:
            out.append('Foreign flows: FIIs were net sellers of about Rs ' + n(abs(fii)) + ' crore. This matters in three ways. First, FIIs own large stakes in the heavyweights that dominate Nifty and Sensex (private banks, IT, energy), so sustained selling pulls the index down directly. Second, foreign selling usually comes with rupee weakness, because sale proceeds are converted back into dollars; a weaker rupee raises the cost of imported oil and inflation worries, which keeps foreign investors cautious. Third, cash selling tends to be accompanied by hedging in derivatives (index shorts or put buying), which can speed up falls. Heavy-selling days usually show up as weak opens and wider intraday ranges.')
        else:
            out.append('Foreign flows: FIIs were net buyers of about Rs ' + n(abs(fii)) + ' crore. Foreign buying is the most reliable fuel for broad rallies, because it tends to go into the index heavyweights (private banks, IT, large caps), which have the most weight in Nifty and Sensex. It also supports the rupee, which lowers imported-inflation worries and invites further foreign money. Buying that continues for several sessions usually marks a change in trend rather than a one-day blip.')
    if dii is not None:
        if dii > 0:
            out.append('Domestic flows: DIIs were net buyers of about Rs ' + n(abs(dii)) + ' crore. Domestic funds receive steady monthly inflows (SIPs and insurance premiums) that have to be invested, which makes them a natural buyer of dips. This cushions the market when foreigners sell, but it rarely starts a rally on its own.')
        else:
            out.append('Domestic flows: DIIs were net sellers of about Rs ' + n(abs(dii)) + ' crore. Domestic funds sell mostly to book profits or meet redemptions. When they sell into a foreign-led rally it often signals a stretched market; when they sell while foreigners also sell, nobody is absorbing the supply and falls can extend.')
    if fii is not None and dii is not None:
        if fii < 0 and dii > 0:
            absorb = dii / abs(fii) * 100 if fii else 0
            out.append('Net picture: foreign selling is being absorbed by domestic buying, and DIIs bought about ' + format(absorb, '.0f') + '% of what FIIs sold. If domestic buying covers most of the foreign selling the index tends to hold or move sideways; if it covers well under half, dips tend to extend. A widening gap between the two over several sessions is the signal to watch.')
        elif fii > 0 and dii > 0:
            out.append('Net picture: both foreign and domestic institutions were buyers. That is the strongest flow backdrop, and rallies in this setup tend to be broad-based, with dips bought quickly.')
        elif fii > 0 and dii < 0:
            out.append('Net picture: foreigners are buying while domestic funds sell. This is common near short-term highs, when domestic funds book profits. The rally is then dependent on foreign money, which can reverse quickly if global cues turn.')
        else:
            out.append('Net picture: both groups were sellers, the weakest flow backdrop. Bounces in this setup tend to fade unless a fresh trigger (a policy move, a sharp fall in crude, a strong global rally) arrives.')
    if out:
        out.append('What to watch: a single day is noise. Three to five consecutive sessions in the same direction, or a change in the size of flows, is what starts to move the trend. Compare today with the participant positioning below to see whether cash flows and derivatives are telling the same story.')
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


def find_col(df, wanted):
    key = wanted.lower().replace(' ', '')
    for c in df.columns:
        if str(c).lower().replace(' ', '') == key:
            return c
    return None


def participant_data(files):
    if not files:
        return None
    now = files[0][1]
    prev = files[1][1] if len(files) > 1 else None
    out = {}
    for seg, lname, sname in GROUPS:
        for who in WHO:
            try:
                rn = now[now['Client Type'] == who]
                lg = float(rn[find_col(now, lname)].iloc[0])
                sh = float(rn[find_col(now, sname)].iloc[0])
            except Exception:
                continue
            d = {'lg': lg, 'sh': sh, 'net': lg - sh, 'plg': None, 'psh': None, 'pnet': None, 'chg': None}
            if prev is not None:
                try:
                    rp = prev[prev['Client Type'] == who]
                    plg = float(rp[find_col(prev, lname)].iloc[0])
                    psh = float(rp[find_col(prev, sname)].iloc[0])
                    d['plg'], d['psh'], d['pnet'] = plg, psh, plg - psh
                    d['chg'] = (lg - sh) - (plg - psh)
                except Exception:
                    pass
            out[(seg, who)] = d
    return out or None


def participant_table(pdata):
    if not pdata:
        return None
    rows = []
    for seg, _, _ in GROUPS:
        first = True
        for who in WHO:
            d = pdata.get((seg, who))
            if not d:
                continue
            rows.append({'Segment': seg if first else '', 'Participant': who,
                         'Long (today)': d['lg'], 'Short (today)': d['sh'], 'Net (today)': d['net'],
                         'Long (prev day)': d['plg'], 'Short (prev day)': d['psh'],
                         'Net (prev day)': d['pnet'], 'Change in net': d['chg']})
            first = False
    return pd.DataFrame(rows) if rows else None


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
    'bearish': 'net long puts and net short calls, a bearish combination (positioned for a fall or protecting against one)',
    'bullish': 'net long calls and net short puts, a bullish combination (positioned for a rise)',
    'seller': 'net short both calls and puts, which is typical of premium selling: the group expects the market to stay inside a range and is paid for time decay, but loses if a sharp move arrives',
    'buyer': 'net long both calls and puts, which is typical of volatility buying: the group is paying for protection or expects a large move in either direction',
    'mixed': 'a mixed option position without a clear direction',
}


def fut_sentence(who, d):
    sl = share_long(d['lg'], d['sh'])
    s = who + ' index futures: long ' + n(d['lg']) + ', short ' + n(d['sh']) + ', net ' + sgn(d['net']) + ' contracts (long share ' + format(sl, '.0f') + '%'
    if d['plg'] is not None:
        psl = share_long(d['plg'], d['psh'])
        s += ', previous day ' + format(psl, '.0f') + '%'
    s += ').'
    return s, sl


def participant_analysis(pdata):
    if not pdata:
        return []
    out = []

    def g(seg, who):
        return pdata.get((seg, who))

    # ---- FII
    f = g('Index Futures', 'FII')
    if f:
        s, sl = fut_sentence('FII', f)
        if sl is not None:
            if sl <= 30:
                s += ' This is a heavily short stance. It tells us foreigners are bearish or heavily hedged. Two effects follow: on bad news the downside can extend because shorts are already in profit and add more; but a heavy short base is also fuel for a sharp rally, because a positive surprise forces short covering, which pushes the index up quickly.'
            elif sl <= 45:
                s += ' This is a net short lean. Foreigners are cautious but not extreme, so the market can still rise, though rallies tend to meet selling from this group.'
            elif sl < 55:
                s += ' This is close to balanced, so foreign institutions are not giving a strong directional signal through index futures.'
            elif sl < 65:
                s += ' This is a net long lean, a supportive sign: foreign money is positioned for a rise and dips are likely to be bought.'
            else:
                s += ' This is a heavily long stance. It supports the market but also means foreigners have little room to add; a disappointment could trigger long unwinding.'
        if f['chg'] is not None and f['chg'] != 0:
            if f['chg'] > 0:
                s += ' Versus the previous day the net moved by ' + sgn(f['chg']) + ' contracts toward long, through fresh longs or short covering, a mildly supportive change.'
            else:
                s += ' Versus the previous day the net moved by ' + sgn(f['chg']) + ' contracts toward short, through fresh shorts or long unwinding, a mildly negative change.'
        s += ' Index futures are the cleanest gauge of directional conviction because they carry no option premium, which is why this line is tracked more closely than any other.'
        out.append(s)
    fc, fp = g('Index Call Options', 'FII'), g('Index Put Options', 'FII')
    if fc and fp:
        st = opt_stance(fc['net'], fp['net'])
        out.append('FII index options: net call position ' + sgn(fc['net']) + ' and net put position ' + sgn(fp['net']) + ' contracts. This reads as ' + STANCE_TEXT.get(st, '') + '. Compare it with the futures stance above: when options and futures agree, conviction is higher; when they disagree (for example short futures but short puts), the group is often hedging a book or running a spread rather than expressing one view.')
    fs = g('Stock Futures', 'FII')
    if fs:
        s = 'FII stock futures: net ' + sgn(fs['net']) + ' contracts (long ' + n(fs['lg']) + ', short ' + n(fs['sh']) + ')'
        if fs['chg'] is not None:
            s += ', a change of ' + sgn(fs['chg']) + ' on the day'
        s += '. Stock futures show conviction in individual shares rather than the index. Rising stock-future longs alongside a rising index suggest broad participation, which makes a rally healthier; a rising index with falling stock-future longs suggests a narrow, heavyweight-led move that is easier to reverse.'
        out.append(s)

    # ---- DII
    di, ds = g('Index Futures', 'DII'), g('Stock Futures', 'DII')
    if di or ds:
        s = 'DII derivatives: '
        if di:
            s += 'index futures net ' + sgn(di['net']) + ' contracts'
            if di['chg'] is not None:
                s += ' (change ' + sgn(di['chg']) + ')'
            s += '. '
        if ds:
            s += 'Stock futures net ' + sgn(ds['net']) + ' contracts. '
        s += 'Domestic institutions mostly use derivatives to hedge large equity portfolios, so a net short futures position is usually a hedge against their cash holdings and not a bearish call. The useful signal is the change: when DIIs add to hedges it often means they expect near-term weakness, and when they lift hedges they are comfortable holding risk. Because their cash-market buying is steady, they rarely drive the index direction through derivatives.'
        out.append(s)

    # ---- Client
    ci = g('Index Futures', 'Client')
    if ci:
        s, sl = fut_sentence('Client', ci)
        s += ' The Client category is mainly retail and high-net-worth traders. It is a useful crowd gauge because this group is often positioned against institutions near turning points.'
        if f and f['net'] * ci['net'] < 0:
            s += ' Right now clients and FIIs are on opposite sides in index futures. Institutions are generally the better-capitalised and better-informed side, so a large gap means the market is leaning on the institutional view; the risk is that if the crowd side is wrong, it unwinds together and adds momentum to the move.'
        elif f:
            s += ' Clients and FIIs are leaning the same way in index futures, which means the position is less contested and any reversal would come from a news trigger rather than from the two groups clashing.'
        out.append(s)
    cc, cp = g('Index Call Options', 'Client'), g('Index Put Options', 'Client')
    if cc and cp:
        st = opt_stance(cc['net'], cp['net'])
        out.append('Client index options: net call ' + sgn(cc['net']) + ' and net put ' + sgn(cp['net']) + ' contracts, reading as ' + STANCE_TEXT.get(st, '') + '. Retail option buyers lose money more often than they win, so a heavy net-buyer stance means premium sellers (mainly Pro and FII) are on the other side; as expiry approaches, time decay works for the sellers.')

    # ---- Pro
    pi = g('Index Futures', 'Pro')
    pc_, pp = g('Index Call Options', 'Pro'), g('Index Put Options', 'Pro')
    if pi:
        s = 'Pro (proprietary desks): index futures net ' + sgn(pi['net']) + ' contracts'
        if pi['chg'] is not None:
            s += ' (change ' + sgn(pi['chg']) + ')'
        s += '. '
        if pc_ and pp:
            st = opt_stance(pc_['net'], pp['net'])
            s += 'In index options they are ' + STANCE_TEXT.get(st, '') + '. '
        s += 'Pro desks run arbitrage, market-making and hedged option books, so their net number is largely a balancing item that offsets the other groups. Large daily changes usually reflect expiry-related adjustments or hedging of option books, and are best read together with the option open interest below, not as a directional view.'
        out.append(s)

    # ---- overall alignment
    nets = {w: g('Index Futures', w)['net'] for w in WHO if g('Index Futures', w)}
    if len(nets) >= 3:
        longs = [w for w, v in nets.items() if v > 0]
        shorts = [w for w, v in nets.items() if v < 0]
        out.append('Who is on which side (index futures): net long are ' + (', '.join(longs) if longs else 'nobody') + '; net short are ' + (', '.join(shorts) if shorts else 'nobody') + '. Futures are a zero-sum market, so every net long is matched by a net short elsewhere. The informative question is which side is the stronger hand: when foreign institutions and Pro are short while Client is long, the weaker hands hold the long side and a fall tends to be sharper; when FIIs are long against a short retail crowd, a rise tends to be better supported.')
    return out


# ================= OPTION CHAIN / OPEN INTEREST =================
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


def option_chain_sensex():
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
        return df, float(df['Strike'].median())
    except Exception:
        return None, None


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
    rows = []
    for key in nifty:
        rows.append({'Metric': key, 'Nifty 50': nifty.get(key), 'Sensex': sensex.get(key) if sensex else None})
    return pd.DataFrame(rows)


def oi_analysis(name, s, chg_pct=None):
    if not s:
        return []
    out = []
    spot, pw = s['Spot'], s['Highest Put OI strike (support)']
    cw, mp = s['Highest Call OI strike (resistance)'], s['Max pain']
    pcr, ppcr = s['PCR today'], s['PCR previous day']
    cc, pc = s['Change in Call OI'], s['Change in Put OI']

    t = name + ' put-call ratio is ' + str(pcr) + ' (previous day ' + str(ppcr) + '). '
    if pcr >= 1.3:
        t += 'This is a heavily put-weighted chain. Put writers are confident that the market will not fall much, which is supportive; but a very high reading can also mean the market is over-hedged or has already fallen a lot, so it is a mixed signal at extremes. '
    elif pcr >= 1.0:
        t += 'This is a put-leaning chain, normally read as supportive because put writers are defending lower levels. '
    elif pcr >= 0.7:
        t += 'This is a balanced to call-leaning chain, with neither side dominant. '
    else:
        t += 'This is a call-heavy chain. Call writers are capping the upside, which is cautious; at extremes it can mean the market is overbought. '
    t += ('The ratio rose, so put writing is building faster than call writing, a mildly supportive change.' if pcr > ppcr else 'The ratio fell, so call writing is building faster than put writing, a mildly cautious change.' if pcr < ppcr else 'The ratio was unchanged.')
    out.append(t)

    t = 'Fresh positioning: call OI changed by ' + sgn(cc) + ' contracts and put OI by ' + sgn(pc) + '. '
    if chg_pct is not None:
        if chg_pct >= 0 and pc > cc:
            t += 'With the index up on the day and more fresh put OI than call OI, traders are building floors under a rising market, a healthy long-buildup pattern. '
        elif chg_pct >= 0 and cc >= pc:
            t += 'With the index up but more fresh call OI than put OI, call writers are selling into the rally and building a ceiling; the upside may find resistance near the call wall. '
        elif chg_pct < 0 and cc >= pc:
            t += 'With the index down and more fresh call OI than put OI, the pattern points to fresh selling pressure and traders capping rebounds. '
        else:
            t += 'With the index down but more fresh put OI than call OI, put writers are stepping in to defend lower strikes, which can mean the fall is being absorbed; confirm with the next session before reading it as a bottom. '
    else:
        t += ('More new put OI than call OI points to traders building a floor. ' if pc > cc else 'More new call OI than put OI points to traders building a ceiling. ')
    t += 'Open interest shows where traders are putting their money at risk, so a shift in where it builds is often an earlier warning than the price itself.'
    out.append(t)

    if spot:
        down, up = spot - pw, cw - spot
        t = 'Walls: the largest put OI is at ' + n(pw) + ' (' + n(abs(down)) + ' points ' + ('below' if down >= 0 else 'above') + ' spot) and the largest call OI at ' + n(cw) + ' (' + n(abs(up)) + ' points ' + ('above' if up >= 0 else 'below') + ' spot). '
        if down < 0:
            t += 'Spot is already below the put wall, meaning the floor has been breached; put writers are under pressure and may hedge by selling the index, which can accelerate the fall. '
        elif up < 0:
            t += 'Spot is already above the call wall, meaning the ceiling has been breached; call writers are under pressure and may buy back or hedge, which can accelerate the rise. '
        else:
            t += 'Spot sits between the walls, so the market is trading in the zone the option writers are comfortable with. A close outside a wall, especially with that strike open interest shrinking, is the usual sign that the range has failed. '
        t += 'Strikes with large open interest act as magnets and barriers because writers defend them by hedging in the futures market.'
        out.append(t)
        pull = mp - spot
        out.append('Max pain is at ' + n(mp) + ', ' + n(abs(pull)) + ' points ' + ('above' if pull > 0 else 'below') + ' spot. Max pain is the strike where option buyers collectively lose the most, so option writers benefit if expiry settles near it. The pull is weak far from expiry and strengthens in the last two sessions. Treat it as a tendency, not a target: strong flows can override it, but if nothing else is happening the index often drifts toward it into the close.')
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
    below = [int(r['Period (days)']) for _, r in valid.iterrows() if r['Price vs SMA (%)'] <= 0]
    t = name + ' closed at ' + format(last, ',.2f') + ', above ' + str(len(above)) + ' of ' + str(len(valid)) + ' simple moving averages (' + (', '.join(str(a) for a in above) if above else 'none') + ' day). '
    if len(above) == len(valid):
        t += 'Price is above every average, a fully bullish structure. Trend-following funds and institutions are typically long in this state, so pullbacks to the 20 and 50 day averages are normally bought. '
    elif not above:
        t += 'Price is below every average, a fully bearish structure. Trend followers are typically short or underweight, so rallies toward the 20 and 50 day averages tend to meet supply until price reclaims them. '
    elif 200 in above or 100 in above:
        t += 'Price holds above the longer averages but not all the shorter ones, which usually describes a pullback inside a longer uptrend. The market is resting, and the 50 and 100 day averages are the zones where buyers tend to return. '
    else:
        t += 'Price holds above only the shorter averages while the longer ones are overhead, which usually describes a bounce inside a longer downtrend. Rallies face resistance from the 100 and 200 day averages. '
    out.append(t)
    r50 = valid[valid['Period (days)'] == 50]
    if len(r50):
        d50 = float(r50['Price vs SMA (%)'].iloc[0])
        if d50 > 5:
            out.append('Stretch: price is ' + format(d50, '.1f') + '% above its 50-day SMA, a stretched reading. Markets can stay stretched, but the odds of a pause or a pullback toward the average rise, and new buyers get worse risk-reward at this distance.')
        elif d50 < -5:
            out.append('Stretch: price is ' + format(abs(d50), '.1f') + '% below its 50-day SMA, an oversold reading. Sharp relief bounces become more likely at this distance, though in a strong downtrend price can remain below the average for a long time.')
        else:
            out.append('Stretch: price is ' + format(d50, '.1f') + '% from its 50-day SMA, within a normal range, so the trend is not over-extended in either direction.')
    levels = []
    for _, r in df.iterrows():
        for lab, col in [('SMA', 'SMA'), ('EMA', 'EMA')]:
            v = r[col]
            if pd.notna(v):
                levels.append((lab + ' ' + str(int(r['Period (days)'])), float(v)))
    below_l = sorted([x for x in levels if x[1] <= last], key=lambda x: -x[1])[:2]
    above_l = sorted([x for x in levels if x[1] > last], key=lambda x: x[1])[:2]
    t = 'Nearest moving-average levels: '
    if below_l:
        t += 'below price ' + ', '.join(a + ' at ' + n(b) for a, b in below_l) + ' (levels where dips have tended to find buyers); '
    if above_l:
        t += 'above price ' + ', '.join(a + ' at ' + n(b) for a, b in above_l) + ' (levels where rallies may meet sellers). '
    out.append(t.strip())
    s50 = valid[valid['Period (days)'] == 50]['SMA']
    s200 = valid[valid['Period (days)'] == 200]['SMA']
    if len(s50) and len(s200):
        up = float(s50.iloc[0]) > float(s200.iloc[0])
        out.append('The 50-day SMA is ' + ('above' if up else 'below') + ' the 200-day SMA, ' + ('the golden-cross arrangement, a long-term bullish backdrop in which institutions tend to stay invested.' if up else 'the death-cross arrangement, a long-term cautious backdrop in which rallies are often sold.') + ' EMAs react faster than SMAs, so when the EMA sits well away from the SMA at the same period, the recent move has been sharp.')
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
        if nm == 'Dollar Index':
            t = 'Dollar Index ' + d + ' ' + format(abs(c), '.2f') + '% to ' + format(last, '.2f') + '. '
            if c > 0:
                t += 'A stronger dollar tightens global liquidity. Foreign funds find emerging markets less attractive because their returns are eroded when converted back to dollars, so it is usually linked with FII selling in India and rupee weakness. A sustained climb is a headwind for the index, particularly for foreign-heavy sectors such as banks and IT services (where the rupee effect on earnings also matters).'
            else:
                t += 'A weaker dollar loosens global liquidity and tends to encourage flows into emerging markets, supporting the rupee and FII buying. It is a tailwind for Indian equities, especially if it persists for several sessions.'
            out.append(t)
        if nm == 'Brent Crude':
            t = 'Brent crude ' + d + ' ' + format(abs(c), '.2f') + '% to ' + format(last, '.2f') + '. '
            if c > 0:
                t += 'India imports most of its oil, so higher crude widens the import bill and the current-account deficit, weakens the rupee, adds to inflation and squeezes margins of oil marketers, paint makers, airlines and chemicals. It is a headwind for the index; a sharp sustained rise raises the risk of a rate-cut delay.'
            else:
                t += 'Lower crude helps India: a smaller import bill, a firmer rupee, easier inflation and better margins for oil-using sectors. It is a tailwind for the market, though oil producers and upstream companies lose.'
            out.append(t)
        if nm == 'India VIX':
            t = 'India VIX ' + d + ' ' + format(abs(c), '.2f') + '% to ' + format(last, '.2f') + '. '
            if last < 13:
                t += 'This is a low level: the options market expects calm and is pricing cheap protection. Low VIX favours option buyers, but complacency can precede a sudden spike. '
            elif last < 18:
                t += 'This is a normal level, consistent with an orderly market. '
            elif last < 24:
                t += 'This is an elevated level: the market expects bigger swings, premiums are rich and option sellers collect more but carry more risk. '
            else:
                t += 'This is a high level: the market is nervous and expects large moves, which usually accompanies sell-offs. Premiums are expensive and sharp rebounds are common once the fear fades. '
            t += 'A rising VIX with a falling index signals fear; a falling VIX with a rising index signals a comfortable uptrend, while a rising VIX with a rising index suggests traders are buying protection against the rally ending.'
            out.append(t)
        if nm == 'USD/INR':
            t = 'USD/INR ' + d + ' ' + format(abs(c), '.2f') + '% to ' + format(last, '.2f') + '. '
            t += ('A weaker rupee tends to accompany FII selling and raises imported inflation, a headwind, though it helps IT and pharma exporters.' if c > 0 else 'A firmer rupee tends to accompany foreign inflows and eases inflation, a tailwind for the broader market, though it trims exporters earnings.')
            out.append(t)
    return out


# ================= OVERALL CONCLUSION =================
def days_to_expiry(exp):
    try:
        d = dt.datetime.strptime(str(exp), '%d-%b-%Y').date()
        today = dt.datetime.now(IST).date()
        return max((d - today).days, 0), d
    except Exception:
        return None, None


def conclusion(cash, pdata, n_sum, s_sum, ctx_n, ctx_s, extra):
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

    spot = n_sum['Spot'] if n_sum else (ctx_n['price'] if ctx_n else None)
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

    blocks = []

    # ---------- 1. overall read
    p = ['Overall read: the evidence on this page adds up to a ' + bias + ' lean (score ' + format(score, '+.1f') + '; +4 or more reads as bullish, -4 or less as bearish, and between about -1.5 and +1.5 as neutral). The score is a tally of the signals below, each weighted by how much it usually matters (foreign flows and FII index positioning carry the most weight), so read it as a summary of the evidence and not as a forecast.']
    if bull:
        p.append('Supportive factors: ' + '; '.join(bull) + '.')
    if bear:
        p.append('Cautionary factors: ' + '; '.join(bear) + '.')
    if bull and bear:
        p.append('The evidence is mixed. When strong signals point in opposite directions the market is usually in a transition or a range, and the sensible reading is to lean on the levels (the option walls and the averages) more than on the direction.')
    blocks.append(('Overall read', p))

    # ---------- 2. flows and positioning
    p = []
    if fii is not None or dii is not None:
        t = 'Flows: '
        if fii is not None:
            t += 'foreign institutions were net ' + ('buyers' if fii > 0 else 'sellers') + ' of Rs ' + n(abs(fii)) + ' crore in cash'
        if dii is not None:
            t += (' while domestic institutions were net ' + ('buyers' if dii > 0 else 'sellers') + ' of Rs ' + n(abs(dii)) + ' crore') if fii is not None else 'domestic institutions were net ' + ('buyers' if dii > 0 else 'sellers') + ' of Rs ' + n(abs(dii)) + ' crore'
        t += '. Cash flows show actual investing; derivatives show how institutions are hedging and speculating around it. The two together tell whether the move has real money behind it.'
        p.append(t)
    if f and sl is not None:
        t = 'Positioning: FIIs hold a ' + format(sl, '.0f') + '% long share in index futures. '
        if sl <= 45:
            t += 'They are net short, so the market is vulnerable to further selling if bad news arrives, but a short base this large also leaves room for a sharp short-covering rally if sentiment improves. '
        elif sl >= 55:
            t += 'They are net long, which supports dips, though it leaves less room for new foreign buying to push the market higher. '
        else:
            t += 'This is close to balanced, so FIIs are not leaning heavily either way. '
        ci = pdata.get(('Index Futures', 'Client'))
        if ci and f['net'] * ci['net'] < 0:
            t += 'Retail clients are on the opposite side of FIIs, which historically has favoured the institutional side at turning points.'
        p.append(t)
    if fst:
        p.append('Index options: FIIs are ' + STANCE_TEXT.get(fst, '') + '.')
    if p:
        blocks.append(('Flows and positioning', p))

    # ---------- 3. open interest and expiry map
    exp_str = n_sum['Nearest expiry'] if n_sum else None
    dte, exp_date = days_to_expiry(exp_str) if exp_str else (None, None)
    p = []
    if n_sum and spot:
        pw, cw, mp = n_sum['Highest Put OI strike (support)'], n_sum['Highest Call OI strike (resistance)'], n_sum['Max pain']
        t = 'Expiry map for the ' + str(exp_str) + ' Nifty expiry: spot ' + format(spot, ',.2f') + ', largest put wall ' + n(pw) + ', largest call wall ' + n(cw) + ', max pain ' + n(mp) + ', PCR ' + str(n_sum['PCR today']) + '. '
        if exp_date is not None:
            t += 'Expiry falls on ' + exp_date.strftime('%A, %d %B %Y') + ((' (' + str(dte) + ' calendar day' + ('' if dte == 1 else 's') + ' away)') if dte else ' (today)') + '. Because NSE shifts an expiry back to the previous trading day when the Tuesday falls on a holiday, this date comes straight from the live option chain. '
        t += 'The walls mark where option writers have the most at stake, and writers defend those strikes by hedging, which is why price tends to stay between them unless a strong trigger arrives. Near expiry, time decay accelerates and small moves in the index create large percentage swings in option premiums (high gamma), so the final sessions are more volatile in premium terms than the index itself.'
        p.append(t)
        if s_sum:
            p.append('Sensex open interest: PCR ' + str(s_sum['PCR today']) + ', largest put wall ' + n(s_sum['Highest Put OI strike (support)']) + ', largest call wall ' + n(s_sum['Highest Call OI strike (resistance)']) + ', max pain ' + n(s_sum['Max pain']) + '. A similar PCR to Nifty means both indices are telling the same story; a divergence would suggest the heavyweights common to both are driving one more than the other.')
    if p:
        blocks.append(('Open interest and the expiry map', p))

    # ---------- 4. trend and macro
    p = []
    for nm, c in [('Nifty 50', ctx_n), ('Sensex', ctx_s)]:
        if not c:
            continue
        t = nm + ' closed at ' + n(c['price']) + ', ' + format(abs(c['chg_pct']), '.2f') + '% ' + ('up' if c['chg_pct'] >= 0 else 'down') + ' on the day, ' + format(c['ret5'], '+.1f') + '% over five sessions and ' + format(c['ret20'], '+.1f') + '% over twenty. Its 50-day SMA is ' + n(c['sma50']) + ' and its 200-day SMA ' + n(c['sma200']) + '. The 52-week range is ' + n(c['lo52']) + ' to ' + n(c['hi52']) + ', so price is ' + format((1 - c['price'] / c['hi52']) * 100, '.1f') + '% below the high. '
        if c['price'] > c['sma50'] and c['price'] > c['sma200']:
            t += 'The primary trend remains up while price holds above the 50-day average; that average is the level trend followers watch.'
        elif c['price'] > c['sma200']:
            t += 'Price is below the 50-day average but above the 200-day, so the market is correcting inside a larger uptrend; the 200-day is the key long-term line.'
        else:
            t += 'Price is below the 200-day average, a weak long-term structure in which rallies are more likely to be sold.'
        p.append(t)
    mac = []
    if dxy is not None:
        mac.append('the dollar index ' + ('rose' if dxy > 0 else 'fell') + ' ' + format(abs(dxy), '.2f') + '%')
    if brent is not None:
        mac.append('Brent crude ' + ('rose' if brent > 0 else 'fell') + ' ' + format(abs(brent), '.2f') + '%')
    vix_last = extra_value(extra, 'India VIX')
    if vix_last is not None:
        mac.append('India VIX is at ' + format(vix_last, '.1f'))
    if mac:
        p.append('Macro backdrop: ' + ', '.join(mac) + '. A firmer dollar and higher crude are the two global factors that most often hurt Indian equities (through foreign selling, rupee weakness and inflation), while a softer dollar and lower crude help.')
    if p:
        blocks.append(('Trend and macro backdrop', p))

    # ---------- 5. where could Nifty go by the next expiry
    p = []
    atr = ctx_n['atr'] if ctx_n else None
    sd = None
    method = ''
    if spot:
        if vix_last is not None and 5 < vix_last < 60:
            sd = spot * (vix_last / 100.0) * math.sqrt(max(dte if dte is not None else 1, 1) / 365.0)
            method = 'India VIX of ' + format(vix_last, '.1f')
        elif atr:
            days = max(round((dte if dte is not None else 1) * 5 / 7), 1)
            sd = atr * math.sqrt(days)
            method = 'the 14-day ATR of ' + n(atr) + ' points'
    if sd and n_sum:
        pw, cw, mp = n_sum['Highest Put OI strike (support)'], n_sum['Highest Call OI strike (resistance)'], n_sum['Max pain']
        tilt = sd * max(-0.5, min(0.5, score / 8.0))
        center = 0.75 * (spot + tilt) + 0.25 * mp
        lo, hi = center - 0.6 * sd, center + 0.6 * sd
        p.append('Method: the expected move to expiry is spot times volatility times the square root of time. Using ' + method + ', the one-standard-deviation range is about ' + n(spot - sd) + ' to ' + n(spot + sd) + ' (a move of roughly ' + n(sd) + ' points), and about two-thirds of outcomes should fall inside it if the options market has priced volatility correctly. The option walls give a second view: ' + n(pw) + ' to ' + n(cw) + '.')
        p.append('My base case: Nifty is most likely to spend the run to expiry and settle between about ' + n(lo) + ' and ' + n(hi) + ', centred near ' + n(center) + ', with a ' + ('mild upward' if lean == 'up' else ('mild downward' if lean == 'down' else 'neutral')) + ' tilt. The centre blends the current spot with a small tilt for the ' + bias + ' score and a quarter weighting toward max pain at ' + n(mp) + ', which is how the pull of the writers shows up in the estimate. It is a probabilistic zone, not a promise, and I cannot know the news that will arrive before expiry.')
        bull_target = max(spot + 1.25 * sd, cw + 0.5 * sd)
        bear_target = min(spot - 1.25 * sd, pw - 0.5 * sd)
        p.append('Bullish scenario: a close above the call wall at ' + n(cw) + ', backed by FII cash buying and a falling VIX, would mean the writers defending that strike are wrong-footed and must hedge by buying. That can carry price toward ' + n(bull_target) + ' by expiry. Bearish scenario: a close below the put wall at ' + n(pw) + ', especially with FII selling and rising VIX, would force put writers to hedge by selling and could extend the fall toward ' + n(bear_target) + '. The moving averages give additional reference points: 20-day SMA ' + n(ctx_n['sma20']) + ', 50-day SMA ' + n(ctx_n['sma50']) + ', 200-day SMA ' + n(ctx_n['sma200']) + '.')
    else:
        p.append('The expected-move estimate needs both the option chain and price history, and one of them could not be fetched this morning, so no expiry range is shown today.')
    blocks.append(('Where Nifty could go by the next expiry', p))

    # ---------- 6. Sensex
    p = []
    if ctx_s:
        t = 'Sensex is at ' + n(ctx_s['price']) + ' with a daily ATR of about ' + n(ctx_s['atr']) + ' points. Sensex weekly options expire on Thursdays, so its decay and positioning cycle runs a few days after Nifty. Because Sensex is made of 30 of the same heavyweights that dominate Nifty, it usually follows the same direction; the practical difference is that its option market is thinner, so levels from its chain are less reliable than Nifty levels.'
        p.append(t)
        if ctx_s['atr']:
            p.append('A rough one-day Sensex range from volatility is ' + n(ctx_s['price'] - ctx_s['atr']) + ' to ' + n(ctx_s['price'] + ctx_s['atr']) + '. Moves beyond this are larger than normal and deserve attention.')
        blocks.append(('Sensex view', p))

    # ---------- 7. trading approach
    p = []
    if sd and n_sum and spot:
        pw, cw, mp = n_sum['Highest Put OI strike (support)'], n_sum['Highest Call OI strike (resistance)'], n_sum['Max pain']
        w = max(100, rnd(sd * 0.5))
        sp = rnd(min(pw, spot - 0.8 * sd))
        sc = rnd(max(cw, spot + 0.8 * sd))
        atm = rnd(spot)
        p.append('How I would approach this expiry if I were trading it. The data gives a ' + bias + ' lean, an expected range and defined walls, which favours defined-risk structures over naked positions, because I cannot rule out a gap on news. Everything below is an illustration derived mechanically from the open interest walls and the implied range; it ignores live premiums, margin, liquidity and your risk limits, so treat it as a template to test, not an instruction.')
        if lean == 'flat':
            p.append('Primary idea, range-bound lean: an iron condor. Sell the ' + n(sp) + ' put and the ' + n(sc) + ' call (outside both the OI walls and roughly 0.8 of the implied move) and buy the ' + n(sp - w) + ' put and ' + n(sc + w) + ' call as protection. The structure profits if Nifty stays between ' + n(sp) + ' and ' + n(sc) + ' and decay erodes the premium; the maximum loss is capped at the wing width (' + n(w) + ' points) minus the credit.')
        elif lean == 'up':
            p.append('Primary idea, ' + bias + ' lean: a bull put spread. Sell the ' + n(sp) + ' put (below the put wall and about 0.8 of the implied move under spot) and buy the ' + n(sp - w) + ' put. This profits if Nifty stays above ' + n(sp) + ' and collects decay, so it can win even if the market goes nowhere. If you prefer to own direction, a bull call spread (buy the ' + n(atm) + ' call, sell the ' + n(max(rnd(cw), atm + w)) + ' call) benefits from a rise toward the call wall with limited cost.')
        else:
            p.append('Primary idea, ' + bias + ' lean: a bear call spread. Sell the ' + n(sc) + ' call (above the call wall and about 0.8 of the implied move over spot) and buy the ' + n(sc + w) + ' call. This profits if Nifty stays below ' + n(sc) + ' and collects decay. If you prefer to own direction, a bear put spread (buy the ' + n(atm) + ' put, sell the ' + n(min(rnd(pw), atm - w)) + ' put) benefits from a fall toward the put wall with limited cost.')
        p.append('Risk management: (1) Size so that the maximum loss on the whole structure is a small fixed share of capital, commonly one to two percent. (2) Check the credit: if a spread pays less than about a third of its width, the reward does not compensate the risk, and it is better to skip. (3) Define exits before entry: if Nifty closes beyond the short strike, or beyond the matching OI wall, close the position rather than adjusting repeatedly. (4) Respect time: gamma rises sharply in the last session, so reduce or close short-option structures before the final afternoon instead of holding to settlement. (5) Avoid naked short options around events; the walls only hold while writers are comfortable, and a gap through them is when losses are largest.')
        p.append('Things that would make me stand aside: a very low premium environment (India VIX under about 12, where selling premium pays little), a major scheduled event before expiry, or a one-sided flow day that breaks the range before the trade is even placed.')
    else:
        p.append('A trading framework needs the option chain and volatility data, and they could not be fetched this morning, so no structures are shown today.')
    blocks.append(('Ideal trading approach for the next expiry', p))

    # ---------- 8. what changes it + disclaimer
    p = ['What would change this view: a sharp reversal in FII cash flows or FII index futures positioning, a jump in India VIX of more than about 10 percent, a large move in crude or the dollar, a close beyond either option wall, or a break below the 50-day average. The dashboard rebuilds every morning, so reassess with the fresh numbers each day instead of holding a view fixed.',
         'Important: this is an analytical read of public data, produced by software that follows fixed rules. It is not a prediction or a recommendation, and I am not a licensed investment advisor. Markets can and do move outside any range. Please test any idea on paper first and take your own decisions with your own risk limits.']
    blocks.append(('What would change this, and a note on limits', p))
    return blocks


CSS = ('body{font-family:Segoe UI,Arial;background:#0f172a;color:#e2e8f0;margin:0;padding:16px;max-width:1100px;margin:auto}'
       'h1{margin:0 0 4px 0}h2{border-left:5px solid #38bdf8;padding-left:10px;margin-top:34px}'
       'h3{color:#38bdf8;margin:18px 0 4px 0}'
       '.scroll{overflow-x:auto}'
       'table{border-collapse:collapse;width:100%;margin:8px 0;font-size:14px;background:#111c33}'
       'th{background:#1e293b;color:#38bdf8;text-align:left;padding:9px;white-space:nowrap}'
       'td{padding:8px;border-bottom:1px solid #1e293b;white-space:nowrap}tr:nth-child(even) td{background:#0f1a30}'
       '.pos{color:#22c55e;font-weight:600}.neg{color:#ef4444;font-weight:600}'
       '.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}'
       '.card{background:#1e293b;border-radius:10px;padding:12px;font-size:14px}'
       '.analysis{background:#13233f;border-left:4px solid #f59e0b;border-radius:6px;padding:8px 14px;margin:8px 0 14px 0;font-size:14px}'
       '.analysis b{color:#fbbf24}.analysis p{margin:8px 0;line-height:1.6}'
       '.na{color:#f59e0b}')


def main():
    now = dt.datetime.now(IST).strftime('%d %b %Y, %H:%M IST')
    cash = safe(fii_dii_cash)
    files = safe(last_two_files)
    pdata = safe(participant_data, files) if files else None
    n_df, n_spot = safe(option_chain_nse, 'NIFTY') or (None, None)
    s_df, s_spot = safe(option_chain_sensex) or (None, None)
    hist = {name: safe(get_history, sym) for name, sym in INDICES}
    ctx = {name: safe(market_context, hist[name]) for name, _ in INDICES}
    ctx_n, ctx_s = ctx['Nifty 50'], ctx['Sensex']
    extra = safe(extra_table)

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

    if files:
        title = 'Derivatives positions by participant (' + files[0][0] + (', compared with ' + files[1][0] if len(files) > 1 else '') + ')'
        page += tag('h2', title)
        ptable = safe(participant_table, pdata)
        page += table_html(ptable, ('Net (today)', 'Net (prev day)', 'Change in net'))
        page += note(safe(participant_analysis, pdata) or [])
    else:
        page += tag('h2', 'Derivatives positions by participant') + tag('p', 'NSE participant file could not be fetched.', 'na')

    exp = n_sum['Nearest expiry'] if n_sum else 'unavailable'
    page += tag('h2', 'Open interest: Nifty 50 vs Sensex (nearest expiry ' + str(exp) + ')')
    if n_sum:
        page += table_html(oi_compare(n_sum, s_sum))
        if s_sum is None:
            page += tag('p', 'Sensex option data could not be fetched from BSE this morning.', 'na')
        page += note((safe(oi_analysis, 'Nifty', n_sum, ctx_n['chg_pct'] if ctx_n else None) or []) +
                     (safe(oi_analysis, 'Sensex', s_sum, ctx_s['chg_pct'] if ctx_s else None) or []))
        around = n_near[(n_near['Strike'] > n_spot - 500) & (n_near['Strike'] < n_spot + 500)][['Strike', 'Call OI', 'Call Chg OI', 'Put OI', 'Put Chg OI']]
        page += tag('h3', 'Nifty strike-wise open interest near spot') + table_html(around, ('Call Chg OI', 'Put Chg OI'))
    else:
        page += tag('p', 'NSE option chain could not be fetched this morning.', 'na')

    page += tag('h2', 'Moving averages (SMA and EMA)')
    for name, _ in INDICES:
        last, df = ma_table(hist[name])
        page += tag('h3', name) + table_html(df, ('Price vs SMA (%)', 'Price vs EMA (%)')) + note(safe(ma_analysis, name, last, df) or [])

    page += tag('h2', 'Dollar, crude and volatility') + table_html(extra, ('Change', 'Change %')) + note(safe(extra_analysis, extra) or [])

    page += tag('h2', 'Overall conclusion and next-expiry view')
    res = safe(conclusion, cash, pdata, n_sum, s_sum, ctx_n, ctx_s, extra)
    if res:
        for title, paras in res:
            page += note(paras, title)
    else:
        page += tag('p', 'Conclusion could not be built this morning.', 'na')

    page += tag('p', 'Data from NSE, BSE and Yahoo Finance public pages. Figures may be delayed or missing. For study only, not trading advice.', 'na')
    page += LT + '/body' + GT + LT + '/html' + GT
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(page)
    print('index.html written at', now)


if __name__ == '__main__':
    main()
