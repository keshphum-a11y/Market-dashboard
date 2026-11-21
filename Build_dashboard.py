import datetime as dt
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

LEGEND = [
('FII, DII, Client, Pro', 'FII is foreign institutions, DII is domestic institutions (mutual funds, insurers), Client is retail and other traders, Pro is proprietary trading desks of brokers.'),
('Long, Short, Net', 'Long means contracts bought, a bet on a rise. Short means contracts sold, a bet on a fall. Net is long minus short.'),
('Index vs stock segments', 'Index futures and options track Nifty or Bank Nifty. Stock futures and options track single shares. This data is by category, not by stock name.'),
('Open interest (OI)', 'Number of contracts still open. Rising OI with rising price usually means fresh buying; rising OI with falling price usually means fresh selling.'),
('Put-call ratio (PCR)', 'Put OI divided by call OI. Above 1 is often read as bullish support, below 0.7 as bearish.'),
('Max pain', 'The strike where option buyers collectively lose the most. Prices often drift toward it near expiry.'),
('SMA and EMA', 'Moving averages of closing price over N days. SMA is the plain average; EMA weights recent days more. Price above them suggests an uptrend.'),
('Support and resistance', 'Support is a price zone where falls have repeatedly stopped. Resistance is a zone where rises have repeatedly stalled. Zones here come from swing highs and lows over the last year.'),
('Fibonacci levels', 'Retracements show how deep a pullback might go inside the last rally (38.2%, 50%, 61.8%). Extensions project how far the move could stretch beyond the last high (127.2%, 161.8%).'),
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
if isinstance(v, (int, float, np.integer, np.floating)):
v = float(v)
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
return tag('table', head + body)


def note(paras, title='Analysis'):
paras = [p for p in paras if p]
if not paras:
return ''
return tag('div', tag('b', title) + ''.join(tag('p', p) for p in paras), 'analysis')


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


def cash_analysis(df):
if df is None:
return []
fii = dii = None
for _, r in df.iterrows():
cat = str(r['Category']).upper()
if 'FII' in cat or 'FPI' in cat:
fii = r['Net (Rs cr)']
if 'DII' in cat:
dii = r['Net (Rs cr)']
out = []
if fii is not None:
out.append('What happened: FIIs were net ' + ('buyers' if fii > 0 else 'sellers') + ' of about Rs ' + format(abs(fii), ',.0f') + ' crore in the cash market. Foreign money is the single biggest swing factor for the index, so the sign of this number sets the tone for the day.')
if dii is not None:
out.append('Domestic side: DIIs were net ' + ('buyers' if dii > 0 else 'sellers') + ' of about Rs ' + format(abs(dii), ',.0f') + ' crore.')
if fii is not None and dii is not None:
if fii < 0 and dii > 0:
out.append('Reading: this is the classic tug of war. Foreign selling is being absorbed by domestic buying. If DII buying is larger than FII selling, the index usually holds up; if it falls short, dips tend to extend. Watch whether the gap between the two widens over the next few sessions.')
elif fii > 0 and dii > 0:
out.append('Reading: both groups are buying, which is a strong supportive backdrop. Rallies in this setup tend to be broad-based and easier to sustain.')
elif fii > 0 and dii < 0:
out.append('Reading: foreigners are buying while domestic funds book profits. Often seen near short-term highs; the rally is led by foreign money, which can reverse quickly if global cues turn.')
else:
out.append('Reading: both groups are selling, a weak backdrop. Bounces in this setup often fade unless a fresh trigger arrives.')
out.append('What would change this: one day of flows is noise. Three to five consecutive sessions in the same direction is a trend worth respecting.')
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


def participant_table(files, who):
now = files[0][1]
prev = files[1][1] if len(files) > 1 else None
row_now = now[now['Client Type'] == who]
row_prev = prev[prev['Client Type'] == who] if prev is not None else None
if len(row_now) == 0:
return None
rows = []
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


def participant_view(df):
"""Returns dict segment -> (net, change)."""
out = {}
if df is None:
return out
for _, r in df.iterrows():
out[r['Segment']] = (float(r['Net (Long - Short)']), float(r['Change in net']) if 'Change in net' in df.columns and pd.notna(r.get('Change in net')) else None)
return out


def participant_analysis(df, who):
v = participant_view(df)
if not v:
return []
out = []
fut = v.get('Index Futures')
if fut:
net, ch = fut
s = who + ' index futures: net ' + ('long' if net > 0 else 'short') + ' ' + format(int(abs(net)), ',') + ' contracts'
if ch is not None:
s += ', which is ' + ('more long' if ch > 0 else 'more short') + ' by ' + format(int(abs(ch)), ',') + ' versus the previous day'
s += '. Index futures are the cleanest read of directional conviction because they carry no premium decay.'
out.append(s)
cl, pt = v.get('Index Call Options'), v.get('Index Put Options')
if cl and pt:
out.append('Index options: net call position ' + format(int(cl[0]), ',') + ' and net put position ' + format(int(pt[0]), ',') + '. A group that is net short calls and net short puts together is usually writing options (collecting premium, expecting a range); a group net long both is buying volatility. Net long puts with short calls leans bearish, and the reverse leans bullish.')
sf = v.get('Stock Futures')
if sf:
net, ch = sf
s = 'Stock futures: net ' + ('long' if net > 0 else 'short') + ' ' + format(int(abs(net)), ',') + ' contracts'
if ch is not None:
s += ', changed by ' + format(int(ch), ',') + ' on the day'
s += '. Rising stock-future longs alongside a rising index suggests broad participation; a rising index with falling stock-future longs suggests a narrow, heavyweight-led move.'
out.append(s)
if who == 'FII' and fut:
out.append('Why FII matters most: foreigners are the marginal price setters. Their index-futures long to short ratio is a widely tracked sentiment gauge; a ratio below about 0.3 is generally read as heavily short, above 0.6 as comfortably long. Treat the numbers as context, not a signal on their own.')
if who == 'Client':
out.append('Retail and other clients are often positioned opposite to institutions at turning points, so a large gap between Client and FII positioning is worth noting.')
if who == 'Pro':
out.append('Pro desks are mostly arbitrage and market-making, so their net position is a hedge-adjusted number. Sudden large changes sometimes hint at expiry-related adjustments.')
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


def oi_analysis(name, s):
if not s:
return []
out = []
pcr, ppcr = s['PCR today'], s['PCR previous day']
out.append(name + ' PCR moved from ' + str(ppcr) + ' to ' + str(pcr) + '. ' + ('A rising PCR means put writers (who are bullish or neutral) are adding faster than call writers, a mildly supportive shift.' if pcr > ppcr else 'A falling PCR means call writers (who are bearish or neutral) are adding faster than put writers, a mildly cautious shift.') + ' Above 1.2 can signal an oversold, support-heavy market; below 0.7 an overbought, resistance-heavy one.')
cc, pc = s['Change in Call OI'], s['Change in Put OI']
out.append('Fresh positioning: call OI changed by ' + format(int(cc), ',') + ' and put OI by ' + format(int(pc), ',') + '. ' + ('More new put OI than call OI points to traders building a floor.' if pc > cc else 'More new call OI than put OI points to traders building a ceiling.'))
out.append('Walls: the largest put OI sits at ' + format(int(s['Highest Put OI strike (support)']), ',') + ' (likely floor) and the largest call OI at ' + format(int(s['Highest Call OI strike (resistance)']), ',') + ' (likely ceiling). Max pain is ' + format(int(s['Max pain']), ',') + ' against spot ' + format(s['Spot'], ',.2f') + '. As expiry approaches, price often gravitates toward max pain and stays inside the two walls unless a strong trigger breaks one.')
return out


# ================= MOVING AVERAGES =================
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
out = [name + ' closed at ' + format(last, ',.2f') + ', above ' + str(len(above)) + ' of 5 SMAs (' + (', '.join(str(a) for a in above) if above else 'none') + ' day).']
if len(above) == 5:
out.append('Reading: price is above every average, a fully bullish structure. Pullbacks to the 20 and 50 day averages are the usual places where trends in this state find support. Distance matters too: if price is more than 5% above the 50-day SMA, the move is stretched and prone to a pause.')
elif len(above) == 0:
out.append('Reading: price is below every average, a fully bearish structure. Rallies toward the 20 and 50 day averages tend to meet selling until price reclaims them.')
else:
out.append('Reading: mixed structure. Short-term averages (10, 20) show the near-term mood, longer ones (100, 200) show the primary trend. When price is above the long ones but below the short ones, it is typically a pullback inside an uptrend; the reverse is a bounce inside a downtrend.')
s50, s200 = df[df['Period (days)'] == 50]['SMA'].iloc[0], df[df['Period (days)'] == 200]['SMA'].iloc[0]
out.append('The 50-day SMA is ' + ('above' if s50 > s200 else 'below') + ' the 200-day SMA, ' + ('which is the golden cross arrangement, a long-term bullish backdrop.' if s50 > s200 else 'the death cross arrangement, a long-term cautious backdrop.'))
return out


# ================= SUPPORT / RESISTANCE =================
def swing_levels(hi, lo, window=7):
highs, lows = [], []
h, l = hi.values, lo.values
for i in range(window, len(h) - window):
if h[i] == max(h[i - window:i + window + 1]):
highs.append(float(h[i]))
if l[i] == min(l[i - window:i + window + 1]):
lows.append(float(l[i]))
return highs, lows


def cluster(levels, tol=0.008):
levels = sorted(levels)
groups = []
for v in levels:
if groups and abs(v - np.mean(groups[-1])) / np.mean(groups[-1]) <= tol:
groups[-1].append(v)
else:
groups.append([v])
return [(round(float(np.mean(g)), 0), len(g)) for g in groups]


def support_resistance(symbol):
try:
h = yf.Ticker(symbol).history(period='1y')
h = h.dropna(subset=['High', 'Low', 'Close'])
price = float(h['Close'].iloc[-1])
hi_all, lo_all = float(h['High'].max()), float(h['Low'].min())
highs, lows = swing_levels(h['High'], h['Low'])
zones = cluster(highs + lows)
sup = sorted([z for z in zones if z[0] < price], key=lambda z: -z[0])[:4]
res = sorted([z for z in zones if z[0] > price], key=lambda z: z[0])[:4]
# latest major leg: from the lowest low of the last 6 months to the highest high after it
recent = h.tail(130)
low_idx = recent['Low'].idxmin()
after = recent.loc[low_idx:]
leg_low, leg_high = float(recent['Low'].min()), float(after['High'].max())
rng = leg_high - leg_low
fib = {
'Retracement 38.2%': leg_high - 0.382 * rng,
'Retracement 50%': leg_high - 0.5 * rng,
'Retracement 61.8%': leg_high - 0.618 * rng,
'Extension 127.2%': leg_low + 1.272 * rng,
'Extension 161.8%': leg_low + 1.618 * rng,
}
atr_s = pd.concat([h['High'] - h['Low'], (h['High'] - h['Close'].shift()).abs(), (h['Low'] - h['Close'].shift()).abs()], axis=1).max(axis=1)
atr = float(atr_s.rolling(14).mean().iloc[-1])
return {'price': price, 'hi': hi_all, 'lo': lo_all, 'sup': sup, 'res': res,
'leg_low': leg_low, 'leg_high': leg_high, 'fib': fib, 'atr': atr}
except Exception:
return None


def sr_table(sr):
rows = []
price = sr['price']

def add(label, level, note_=''):
rows.append({'Level type': label, 'Level': round(level, 0), 'Distance (points)': round(level - price, 0),
'Distance (%)': round((level / price - 1) * 100, 2), 'Note': note_})
add('Current price', price)
add('1-year high', sr['hi'], 'Highest price in the last year')
for lv, touches in sr['res']:
add('Resistance zone', lv, 'Tested ' + str(touches) + ' time(s)')
for lv, touches in sr['sup']:
add('Support zone', lv, 'Tested ' + str(touches) + ' time(s)')
add('1-year low', sr['lo'], 'Lowest price in the last year')
for k, v in sr['fib'].items():
add('Fibonacci: ' + k, v)
df = pd.DataFrame(rows)
return df.sort_values('Level', ascending=False).reset_index(drop=True)


def sr_analysis(name, sr):
if not sr:
return []
p = sr['price']
out = []
from_high = (p / sr['hi'] - 1) * 100
from_low = (p / sr['lo'] - 1) * 100
out.append(name + ' is at ' + format(p, ',.0f') + ', which is ' + format(abs(from_high), '.1f') + '% below its 1-year high of ' + format(sr['hi'], ',.0f') + ' and ' + format(from_low, '.1f') + '% above its 1-year low of ' + format(sr['lo'], ',.0f') + '. That places it in the ' + ('upper' if (p - sr['lo']) / (sr['hi'] - sr['lo']) > 0.66 else ('middle' if (p - sr['lo']) / (sr['hi'] - sr['lo']) > 0.33 else 'lower')) + ' part of its yearly range.')
if sr['res']:
out.append('Nearest overhead resistance zones: ' + ', '.join(format(int(z[0]), ',') for z in sr['res'][:3]) + '. A close above the first one, ideally on rising volume and rising open interest, is the usual sign that the next zone becomes the target.')
if sr['sup']:
out.append('Nearest support zones: ' + ', '.join(format(int(z[0]), ',') for z in sr['sup'][:3]) + '. A decisive close below the first one opens the way to the next, and zones tested more often tend to be stronger.')
rng = sr['leg_high'] - sr['leg_low']
out.append('The latest major up-leg ran from about ' + format(sr['leg_low'], ',.0f') + ' to ' + format(sr['leg_high'], ',.0f') + '. Typical pullbacks inside such a leg stop near ' + format(sr['fib']['Retracement 38.2%'], ',.0f') + ' (38.2%), ' + format(sr['fib']['Retracement 50%'], ',.0f') + ' (50%) or ' + format(sr['fib']['Retracement 61.8%'], ',.0f') + ' (61.8%). If the leg resumes and breaks the high, the 127.2% and 161.8% extensions at ' + format(sr['fib']['Extension 127.2%'], ',.0f') + ' and ' + format(sr['fib']['Extension 161.8%'], ',.0f') + ' are the textbook upside zones.')
out.append('Daily volatility (ATR) is about ' + format(sr['atr'], ',.0f') + ' points, so a one-day move beyond that is above normal. These are reference zones built from past prices, not predictions.')
return out


# ================= EXTRA CONTEXT =================
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
if df is None:
return []
out = []
for _, r in df.iterrows():
n, c = r['Name'], r['Change %']
d = 'rose' if c > 0 else 'fell'
if n == 'Dollar Index':
out.append('Dollar Index ' + d + ' ' + str(abs(c)) + '%. A stronger dollar usually weighs on the rupee and on FII flows into emerging markets like India; a weaker dollar is generally supportive.')
if n == 'Brent Crude':
out.append('Brent crude ' + d + ' ' + str(abs(c)) + '%. India imports most of its oil, so higher crude widens the import bill, pressures the rupee and inflation, and is a headwind for the market; falling crude is a tailwind.')
if n == 'India VIX':
out.append('India VIX ' + d + ' ' + str(abs(c)) + '%. Rising VIX means options are pricing bigger moves, which makes option buying costlier; falling VIX favours option writers.')
if n == 'USD/INR':
out.append('USD/INR ' + d + ' ' + str(abs(c)) + '%. A weaker rupee tends to go with FII selling; a firmer rupee with inflows.')
return out


# ================= OVERALL CONCLUSION =================
def conclusion(cash, files, n_sum, sr_n, sr_s, extra):
points, score = [], 0
if cash is not None:
for _, r in cash.iterrows():
if 'FII' in str(r['Category']).upper() or 'FPI' in str(r['Category']).upper():
if r['Net (Rs cr)'] > 0:
score += 1
points.append('FII cash flow: net buying (supportive).')
else:
score -= 1
points.append('FII cash flow: net selling (a drag).')
if files:
fii = participant_table(files, 'FII')
v = participant_view(fii)
if 'Index Futures' in v:
net = v['Index Futures'][0]
score += 1 if net > 0 else -1
points.append('FII index futures: net ' + ('long' if net > 0 else 'short') + ' (' + ('supportive' if net > 0 else 'cautious') + ').')
if n_sum:
score += 1 if n_sum['PCR today'] >= 1 else -1
points.append('Nifty PCR ' + str(n_sum['PCR today']) + (' (put-heavy, supportive).' if n_sum['PCR today'] >= 1 else ' (call-heavy, cautious).'))
if sr_n:
above = sr_n['price'] > sr_n['hi'] * 0.97
points.append('Nifty is ' + ('within...
