def conclusion(cash, files, n_sum, sr_n, sr_s, extra):
    points, score = [], 0
    if cash is not None:
        for _, r in cash.iterrows():
            cat = str(r['Category']).upper()
            who = 'FII' if ('FII' in cat or 'FPI' in cat) else ('DII' if 'DII' in cat else None)
            if who:
                buying = r['Net (Rs cr)'] > 0
                score += 1 if buying else -1
                points.append(who + ' cash flow is net ' + ('buying.' if buying else 'selling.'))
    if files:
        v = participant_view(participant_table(files, 'FII'))
        if 'Index Futures' in v:
            net = v['Index Futures'][0]
            score += 1 if net > 0 else -1
            points.append('FII index futures are net ' + ('long.' if net > 0 else 'short.'))
    if n_sum:
        bull = n_sum['PCR today'] >= 1
        score += 1 if bull else -1
        points.append('Nifty put-call ratio is ' + str(n_sum['PCR today']) + (', put-heavy and supportive.' if bull else ', call-heavy and cautious.'))
    if sr_n:
        pos = (sr_n['price'] - sr_n['lo']) / max(sr_n['hi'] - sr_n['lo'], 1)
        if pos > 0.66:
            score += 1
            points.append('Nifty is in the upper third of its one-year range.')
        elif pos < 0.33:
            score -= 1
            points.append('Nifty is in the lower third of its one-year range.')
        else:
            points.append('Nifty is in the middle of its one-year range.')
    if extra is not None:
        for _, r in extra.iterrows():
            c = r['Change %']
            if r['Name'] == 'Dollar Index' and abs(c) > 0.3:
                score += -1 if c > 0 else 1
                points.append('Dollar Index ' + ('rose' if c > 0 else 'fell') + ', ' + ('a headwind' if c > 0 else 'a tailwind') + ' for flows into India.')
            if r['Name'] == 'Brent Crude' and abs(c) > 1.5:
                score += -1 if c > 0 else 1
                points.append('Brent crude ' + ('rose' if c > 0 else 'fell') + ', ' + ('a headwind.' if c > 0 else 'a tailwind.'))
            if r['Name'] == 'India VIX' and c > 5:
                score -= 1
                points.append('India VIX jumped, a sign of rising nervousness.')

    if score >= 3:
        bias = 'bullish'
    elif score >= 1:
        bias = 'mildly bullish'
    elif score == 0:
        bias = 'neutral'
    elif score >= -2:
        bias = 'mildly bearish'
    else:
        bias = 'bearish'

    rows = []
    if n_sum:
        rows.append({'Range': 'Nifty 50: expiry range (' + str(n_sum['Nearest expiry']) + ')',
                     'Low': n_sum['Highest Put OI strike (support)'],
                     'High': n_sum['Highest Call OI strike (resistance)'],
                     'Basis': 'Largest put and call OI walls; max pain ' + format(int(n_sum['Max pain']), ',')})
    for name, sr in [('Nifty 50', sr_n), ('Sensex', sr_s)]:
        if sr:
            rows.append({'Range': name + ': typical one-day range', 'Low': sr['price'] - sr['atr'],
                         'High': sr['price'] + sr['atr'], 'Basis': 'Current price plus or minus one 14-day ATR'})
            lo = sr['sup'][0][0] if sr['sup'] else sr['lo']
            hi = sr['res'][0][0] if sr['res'] else sr['hi']
            rows.append({'Range': name + ': swing range', 'Low': lo, 'High': hi,
                         'Basis': 'Nearest support and resistance zones'})
            rows.append({'Range': name + ': wider range', 'Low': sr['fib']['Retracement 61.8%'],
                         'High': sr['fib']['Extension 127.2%'],
                         'Basis': 'Deep pullback level to first upside extension of the latest leg'})

    paras = []
    paras.append('Overall bias: ' + bias + ' (score ' + str(score) + '). This is a tally of the signals on this page, each weighted equally, so read it as a summary of the evidence and not as a forecast.')
    if points:
        paras.append('What is driving it: ' + ' '.join(points))
    if n_sum:
        paras.append('Expiry view: for the ' + str(n_sum['Nearest expiry']) + ' expiry, the largest put wall is at ' + format(int(n_sum['Highest Put OI strike (support)']), ',') + ' and the largest call wall at ' + format(int(n_sum['Highest Call OI strike (resistance)']), ',') + ', with max pain at ' + format(int(n_sum['Max pain']), ',') + ' against spot ' + format(n_sum['Spot'], ',.2f') + '. Price tends to stay between the walls and drift toward max pain unless FII flows or global cues force a break. A close beyond a wall, especially with that strike\'s open interest shrinking, is the usual sign the range has failed.')
    for name, sr in [('Nifty 50', sr_n), ('Sensex', sr_s)]:
        if not sr:
            continue
        up, dn = sr['res'][:2], sr['sup'][:2]
        text = 'Beyond expiry, ' + name + ' (' + format(sr['price'], ',.0f') + '): '
        if up:
            nxt = format(up[1][0], ',.0f') if len(up) > 1 else format(sr['fib']['Extension 127.2%'], ',.0f')
            text += 'a close above ' + format(up[0][0], ',.0f') + ' would open ' + nxt + ' as the next zone. '
        if dn:
            nxt = format(dn[1][0], ',.0f') if len(dn) > 1 else format(sr['lo'], ',.0f')
            text += 'A close below ' + format(dn[0][0], ',.0f') + ' would expose ' + nxt + '.'
        paras.append(text)
    paras.append('What would change this view: a sharp reversal in FII flows, a jump in India VIX, a big move in crude or the dollar, or a break of the option walls. Reassess every morning, since this page rebuilds each day.')
    paras.append('These ranges are analytical reference zones built from past prices and open interest. They are not predictions or trading advice, and markets can move outside any range.')
    return (pd.DataFrame(rows) if rows else None), paras


CSS = ('body{font-family:Segoe UI,Arial;background:#0f172a;color:#e2e8f0;margin:0;padding:16px}'
       'h1{margin:0 0 4px 0}h2{border-left:5px solid #38bdf8;padding-left:10px;margin-top:34px}'
       'h3{color:#38bdf8;margin:18px 0 4px 0}'
       'table{border-collapse:collapse;width:100%;margin:8px 0;font-size:14px;background:#111c33}'
       'th{background:#1e293b;color:#38bdf8;text-align:left;padding:9px}'
       'td{padding:8px;border-bottom:1px solid #1e293b}tr:nth-child(even) td{background:#0f1a30}'
       '.pos{color:#22c55e;font-weight:600}.neg{color:#ef4444;font-weight:600}'
       '.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}'
       '.card{background:#1e293b;border-radius:10px;padding:12px;font-size:14px}'
       '.analysis{background:#13233f;border-left:4px
