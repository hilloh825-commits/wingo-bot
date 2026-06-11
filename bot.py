import os
import json
import time
import random
import sqlite3
import math
import requests
import asyncio
import numpy as np
from collections import Counter, defaultdict
from datetime import datetime
from scipy import stats as scipy_stats
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ==================== CONFIG ====================
BOT_TOKEN    = os.getenv("BOT_TOKEN")
CHAT_ID      = os.getenv("CHAT_ID", "")
DB_FILE      = "wingo-bot/wingo.db"
AUTO_SEC     = 60
MIN_CONF     = 50

# ==================== APIs (primary + fallback) ====================
API_PRIMARY  = "https://draw.ar-lottery01.com/WinGo/WinGo_1M/GetHistoryIssuePage.json"
API_FALLBACK = ("https://wingolast100.vercel.app/api/results"
                "?typeId=1&apiKey=12a04165-748c-4144-9398-96bd2e0ad956"
                "&token=1a97a413-ff57-4097-a44c-4bd402ace8d5&limit=100")
HEADERS_PRI  = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.sikkimin.com/", "Accept": "application/json"}

# ==================== WINGO RULES ====================
RULES = {
    0: {"color": "red",    "violet": True},
    1: {"color": "green",  "violet": False},
    2: {"color": "red",    "violet": False},
    3: {"color": "green",  "violet": False},
    4: {"color": "red",    "violet": False},
    5: {"color": "green",  "violet": True},
    6: {"color": "red",    "violet": False},
    7: {"color": "green",  "violet": False},
    8: {"color": "red",    "violet": False},
    9: {"color": "green",  "violet": False},
}
def get_class(n):   return "Big" if n >= 5 else "Small"
def get_color(n):   return "violet" if RULES[n]["violet"] else RULES[n]["color"]
def is_big(n):      return n >= 5
def is_small(n):    return n <= 4
def is_violet(n):   return RULES[n]["violet"]

# ==================== DATABASE ====================
def init_db():
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS history (
            period TEXT PRIMARY KEY, number INTEGER,
            size TEXT, color TEXT, ts TEXT);
        CREATE TABLE IF NOT EXISTS predictions (
            period TEXT PRIMARY KEY, pred_num INTEGER,
            pred_size TEXT, pred_color TEXT,
            confidence REAL, models_voted INTEGER,
            actual_num INTEGER, actual_size TEXT,
            result TEXT, ts TEXT);
        CREATE TABLE IF NOT EXISTS model_weights (
            name TEXT PRIMARY KEY, weight REAL DEFAULT 1.0,
            wins INTEGER DEFAULT 0, total INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS streak_stats (
            len INTEGER PRIMARY KEY,
            same_count INTEGER DEFAULT 0,
            opp_count INTEGER DEFAULT 0);
    """)
    # seed streak_stats rows 1-6
    for L in range(1, 7):
        c.execute("INSERT OR IGNORE INTO streak_stats (len, same_count, opp_count) VALUES (?,0,0)", (L,))
    conn.commit(); conn.close()

def db():    return sqlite3.connect(DB_FILE)

def load_weights(names):
    conn = db(); c = conn.cursor()
    w = {}
    for n in names:
        c.execute("SELECT weight, wins, total FROM model_weights WHERE name=?", (n,))
        row = c.fetchone()
        w[n] = {"weight": row[0], "wins": row[1], "total": row[2]} if row else {"weight": 1.0, "wins": 0, "total": 0}
    conn.close(); return w

def save_weights(weights):
    conn = db(); c = conn.cursor()
    for name, d in weights.items():
        c.execute("INSERT OR REPLACE INTO model_weights (name,weight,wins,total) VALUES (?,?,?,?)",
                  (name, d["weight"], d["wins"], d["total"]))
    conn.commit(); conn.close()

def update_weights_from_result(name_vote_map, actual_size, weights):
    for name, voted in name_vote_map.items():
        if voted is None: continue
        weights[name]["total"] += 1
        if voted == actual_size:
            weights[name]["wins"] += 1
        acc = weights[name]["wins"] / max(1, weights[name]["total"])
        # bayesian weight update: 0.3 (floor) … 3.5 (cap)
        weights[name]["weight"] = max(0.3, min(3.5, 0.4 + acc * 3.1))
    save_weights(weights)
    return weights

def load_streak_stats():
    conn = db(); c = conn.cursor()
    c.execute("SELECT len, same_count, opp_count FROM streak_stats")
    rows = {r[0]: {"same": r[1], "opp": r[2]} for r in c.fetchall()}
    conn.close()
    return rows

def update_streak_stats(seq, actual_size):
    if len(seq) < 2: return
    last = get_class(seq[0]); streak = 1
    for n in seq[1:]:
        if get_class(n) == last: streak += 1
        else: break
    L = min(streak, 6)
    conn = db(); c = conn.cursor()
    if actual_size == last:
        c.execute("UPDATE streak_stats SET same_count=same_count+1 WHERE len=?", (L,))
    else:
        c.execute("UPDATE streak_stats SET opp_count=opp_count+1 WHERE len=?", (L,))
    conn.commit(); conn.close()

def get_reversal_prob(streak_len):
    stats = load_streak_stats()
    L = min(streak_len, 6)
    d = stats.get(L, {"same": 0, "opp": 0})
    total = d["same"] + d["opp"]
    if total < 5:
        # fallback priors
        priors = {1: 0.50, 2: 0.52, 3: 0.58, 4: 0.65, 5: 0.72, 6: 0.78}
        return priors.get(L, 0.60)
    return d["opp"] / total

def log_history(period, number):
    conn = db(); c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO history (period,number,size,color,ts) VALUES (?,?,?,?,?)",
              (period, number, get_class(number), get_color(number), datetime.now().isoformat()))
    conn.commit(); conn.close()

def save_prediction(period, pred_num, pred_size, pred_color, conf, models_voted):
    conn = db(); c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO predictions (period,pred_num,pred_size,pred_color,confidence,models_voted,ts) VALUES (?,?,?,?,?,?,?)",
              (period, pred_num, pred_size, pred_color, conf, models_voted, datetime.now().isoformat()))
    conn.commit(); conn.close()

def settle_prediction(period, actual_num):
    actual_size = get_class(actual_num)
    conn = db(); c = conn.cursor()
    c.execute("SELECT pred_size FROM predictions WHERE period=? AND result IS NULL", (period,))
    row = c.fetchone()
    if not row: conn.close(); return None
    result = "WIN" if row[0] == actual_size else "LOSS"
    c.execute("UPDATE predictions SET actual_num=?,actual_size=?,result=? WHERE period=?",
              (actual_num, actual_size, result, period))
    conn.commit(); conn.close()
    return result

def get_pending():
    conn = db(); c = conn.cursor()
    c.execute("SELECT period FROM predictions WHERE result IS NULL")
    rows = [r[0] for r in c.fetchall()]; conn.close(); return rows

def get_pred_info(period):
    conn = db(); c = conn.cursor()
    c.execute("SELECT pred_size,pred_num,actual_size,actual_num,confidence,result FROM predictions WHERE period=?", (period,))
    row = c.fetchone(); conn.close(); return row

def get_history_num(period):
    conn = db(); c = conn.cursor()
    c.execute("SELECT number FROM history WHERE period=?", (period,))
    row = c.fetchone(); conn.close()
    return row[0] if row else None

def get_stats():
    conn = db(); c = conn.cursor()
    c.execute("SELECT COUNT(*),SUM(result='WIN'),SUM(result='LOSS') FROM predictions WHERE result IS NOT NULL")
    row = c.fetchone(); conn.close()
    total = row[0] or 0; wins = row[1] or 0; losses = row[2] or 0
    return total, wins, losses, round(wins/total*100, 1) if total else 0

# ==================== API ====================
def fetch_latest(limit=100):
    # Try primary API first
    try:
        results = []
        for page in range(1, math.ceil(limit/50)+1):
            r = requests.get(API_PRIMARY, headers=HEADERS_PRI,
                             params={"ts": int(time.time()*1000), "pageNo": page, "pageSize": 50},
                             timeout=12)
            if r.status_code != 200: break
            items = r.json().get("data", {}).get("list", [])
            if not items: break
            for it in items:
                try: results.append({"period": it["issueNumber"], "number": int(it["number"])})
                except: pass
            if len(results) >= limit: break
            time.sleep(0.3)
        if results: return results[:limit]
    except Exception as e:
        print(f"Primary API error: {e}")

    # Fallback API
    try:
        r = requests.get(API_FALLBACK, timeout=12)
        if r.status_code == 200:
            raw = r.json()
            items = raw if isinstance(raw, list) else raw.get("data", raw.get("results", []))
            results = []
            for it in items[:limit]:
                try:
                    period = str(it.get("issueNumber") or it.get("period") or it.get("id"))
                    number = int(it.get("number") or it.get("num") or 0)
                    results.append({"period": period, "number": number})
                except: pass
            if results:
                print(f"Fallback API: got {len(results)} items")
                return results
    except Exception as e:
        print(f"Fallback API error: {e}")
    return []

# ==================== ALL MODELS (900+) ====================

# ---- Core tricks (19 + 9 extra = 28) ----
def t_martingale(seq):
    if len(seq)<2: return None
    l,p=get_class(seq[0]),get_class(seq[1])
    if l==p: return {"size":"Small" if l=="Big" else "Big","conf":72}
def t_trend3(seq):
    if len(seq)<3: return None
    a,b,c=get_class(seq[0]),get_class(seq[1]),get_class(seq[2])
    if a==b==c: return {"size":a,"conf":75}
def t_alt(seq):
    if len(seq)<2: return None
    l,p=get_class(seq[0]),get_class(seq[1])
    if l!=p: return {"size":"Small" if l=="Big" else "Big","conf":68}
def t_freq20(seq):
    if len(seq)<20: return None
    big=sum(1 for i in range(20) if is_big(seq[i]))
    b="Big" if big>10 else "Small"
    return {"size":b,"conf":min(85,55+abs(big-10)/10*35)}
def t_momentum(seq):
    if len(seq)<10: return None
    l5=sum(1 for i in range(5) if is_big(seq[i]))
    p5=sum(1 for i in range(5,10) if is_big(seq[i]))
    if l5>p5+1: return {"size":"Big","conf":70}
    if p5>l5+1: return {"size":"Small","conf":70}
def t_pat5(seq):
    if len(seq)<5: return None
    p="".join("B" if is_big(seq[i]) else "S" for i in range(5))
    T={"BBBBB":("Small",75),"SSSSS":("Big",75),"BBBBS":("Small",68),
       "SSSSB":("Big",68),"BBSSB":("Big",66),"SSBBS":("Small",66),
       "BSBSB":("Big",67),"SBSBS":("Small",67)}
    return {"size":T[p][0],"conf":T[p][1]} if p in T else None
def t_streak_rev(seq):
    if len(seq)<2: return None
    lc=get_class(seq[0]); streak=1
    for n in seq[1:]:
        if get_class(n)==lc: streak+=1
        else: break
    if streak>=2:
        rev_p = get_reversal_prob(streak)
        if rev_p>=0.52:
            opp="Small" if lc=="Big" else "Big"
            return {"size":opp,"conf":min(88,50+rev_p*45)}
def t_zigzag(seq):
    if len(seq)<6: return None
    ch=sum(1 for i in range(5) if get_class(seq[i])!=get_class(seq[i+1]))
    if ch>=4: return {"size":"Small" if get_class(seq[0])=="Big" else "Big","conf":74}
def t_fib(seq):
    if len(seq)<10: return None
    fib=[1,1,2,3,5,8,13]; bw=sw=tw=0
    for i in range(min(len(seq),7)):
        w=fib[i]; tw+=w
        if is_big(seq[i]): bw+=w
        else: sw+=w
    b="Big" if bw>sw else "Small"
    return {"size":b,"conf":min(85,55+(max(bw,sw)/tw)*35)}
def t_lawthird(seq):
    if len(seq)<20: return None
    u=len(set(seq[:20]))
    if u<=7: return {"size":"Big","conf":68}
    if u>=13: return {"size":"Small","conf":68}
def t_gap(seq):
    if len(seq)<15: return None
    last=seq[0]; lp=0; gaps=[]
    for i in range(1,min(len(seq),25)):
        if seq[i]==last: gaps.append(i-lp); lp=i
    if len(gaps)>=2:
        avg=sum(gaps)/len(gaps)
        if avg<4: return {"size":"Big","conf":66}
        if avg>8: return {"size":"Small","conf":66}
def t_evenodd(seq):
    if len(seq)<4: return None
    par=seq[0]%2; st=1
    for n in seq[1:]:
        if n%2==par: st+=1
        else: break
    if st>=3: return {"size":"Big" if par==0 else "Small","conf":70}
def t_hot10(seq):
    if len(seq)<10: return None
    big=sum(1 for i in range(10) if is_big(seq[i]))
    if big==5: return None
    return {"size":"Big" if big>5 else "Small","conf":min(80,55+abs(big-5)/5*35)}
def t_movavg(seq):
    if len(seq)<15: return None
    sh=sum(seq[:5])/5; lo=sum(seq[:15])/15
    if sh>lo+0.8: return {"size":"Big","conf":70}
    if sh<lo-0.8: return {"size":"Small","conf":70}
def t_extreme(seq):
    if not seq: return None
    if seq[0]==0: return {"size":"Big","conf":72}
    if seq[0]==9: return {"size":"Small","conf":72}
def t_consec_big(seq):
    bc=0
    for n in seq[:12]:
        if is_big(n): bc+=1
        else: break
    if bc>=8: return {"size":"Small","conf":min(88,72+bc)}
def t_consec_small(seq):
    sc=0
    for n in seq[:10]:
        if is_small(n): sc+=1
        else: break
    if sc>=5: return {"size":"Big","conf":min(85,70+sc)}
def t_palindrome(seq):
    for L in (5,7):
        if len(seq)>=L:
            w=seq[:L]
            if all(w[i]==w[L-1-i] for i in range(L//2)):
                return {"size":get_class(w[L//2]),"conf":70}
def t_violet(seq):
    if len(seq)<4: return None
    no=0
    for n in seq[:6]:
        if not is_violet(n): no+=1
        else: break
    if no>=4: return {"size":"Big","conf":69}

def t_rsi(seq):
    if len(seq)<40: return None
    vals=[1 if is_big(seq[i]) else 0 for i in range(min(80,len(seq)))]
    gains=sum(max(0,vals[i]-vals[i-1]) for i in range(1,len(vals)))
    losses=sum(max(0,vals[i-1]-vals[i]) for i in range(1,len(vals)))
    rsi=100-(100/(1+gains/max(1e-9,losses)))
    if rsi>65: return {"size":"Big","conf":74}
    if rsi<35: return {"size":"Small","conf":74}
def t_macd(seq):
    if len(seq)<50: return None
    v=[1 if is_big(seq[i]) else 0 for i in range(min(100,len(seq)))]
    e12=sum(v[:12])/12; e26=sum(v[:26])/26
    macd=e12-e26; sig=sum(v[:9])/9-sum(v[:26])/26
    if macd>sig: return {"size":"Big","conf":70}
    if macd<sig: return {"size":"Small","conf":70}
def t_bayesian(seq):
    if len(seq)<40: return None
    prior=sum(is_big(n) for n in seq)/len(seq)
    last20=sum(is_big(n) for n in seq[:20])/20
    post=prior*last20/0.5
    if post>0.7: return {"size":"Big","conf":min(85,post*100)}
    if post<0.3: return {"size":"Small","conf":min(85,(1-post)*100)}
def t_autocorr(seq):
    if len(seq)<60: return None
    vals=np.array([1 if is_big(seq[i]) else 0 for i in range(min(200,len(seq)))])
    bc,blag=0,5
    for lag in range(4,30):
        try:
            c=float(np.corrcoef(vals[:-lag],vals[lag:])[0,1])
            if not math.isnan(c) and abs(c)>bc: bc=abs(c); blag=lag
        except: pass
    if bc>0.35:
        recent=[1 if is_big(seq[i]) else 0 for i in range(min(blag,len(seq)))]
        pred="Big" if sum(recent)/len(recent)>0.5 else "Small"
        return {"size":pred,"conf":min(82,55+bc*45)}
def t_entropy(seq):
    if len(seq)<30: return None
    last30=seq[:30]
    cnt=Counter(last30)
    probs=[c/30 for c in cnt.values()]
    ent=-sum(p*math.log2(p) for p in probs)
    if ent<2.0:
        most=cnt.most_common(1)[0][0]
        return {"size":get_class(most),"conf":72}
def t_hot_num(seq):
    if len(seq)<20: return None
    hot=Counter(seq[:20]).most_common(1)[0][0]
    if seq[0]==hot: return {"size":"Small" if is_big(hot) else "Big","conf":67}
def t_cold_num(seq):
    if len(seq)<30: return None
    appeared=set(seq[:30])
    cold=[n for n in range(10) if n not in appeared]
    if cold: return {"size":get_class(cold[0]),"conf":65}
def t_window3(seq):
    if len(seq)<30: return None
    s1=sum(is_big(seq[i]) for i in range(10))
    s2=sum(is_big(seq[i]) for i in range(10,20))
    s3=sum(is_big(seq[i]) for i in range(20,30))
    if s1>s2>s3: return {"size":"Small","conf":68}
    if s1<s2<s3: return {"size":"Big","conf":68}
def t_double_flip(seq):
    if len(seq)<2: return None
    l=get_class(seq[0]); p=get_class(seq[1])
    if l==p: return {"size":"Small" if l=="Big" else "Big","conf":71}
def t_sector(seq):
    if len(seq)<40: return None
    sec=[sum(is_big(seq[i]) for i in range(j,j+10)) for j in range(0,40,10)]
    if sec[0]>sec[1]>sec[2]>sec[3]: return {"size":"Small","conf":72}
    if sec[0]<sec[1]<sec[2]<sec[3]: return {"size":"Big","conf":72}
def t_long_trend(seq):
    if len(seq)<50: return None
    big=sum(is_big(n) for n in seq[:50])
    if big>30: return {"size":"Big","conf":min(80,55+(big-25)*2)}
    if big<20: return {"size":"Small","conf":min(80,55+(25-big)*2)}
def t_vol(seq):
    if len(seq)<20: return None
    vals=[1 if is_big(seq[i]) else 0 for i in range(20)]
    changes=sum(1 for i in range(1,20) if vals[i]!=vals[i-1])
    if changes<=5:
        return {"size":"Big" if vals[-1]==1 else "Small","conf":74}
    if changes>=15:
        return {"size":"Small" if vals[-1]==1 else "Big","conf":68}

CORE_TRICKS = [
    ("Martingale",  t_martingale), ("Trend3",     t_trend3),
    ("Alt",         t_alt),        ("Freq20",     t_freq20),
    ("Momentum",    t_momentum),   ("Pat5",       t_pat5),
    ("StreakRev",   t_streak_rev), ("Zigzag",     t_zigzag),
    ("Fib",         t_fib),        ("LawThird",   t_lawthird),
    ("Gap",         t_gap),        ("EvenOdd",    t_evenodd),
    ("Hot10",       t_hot10),      ("MovAvg",     t_movavg),
    ("Extreme",     t_extreme),    ("ConsecBig",  t_consec_big),
    ("ConsecSmall", t_consec_small),("Palindrome", t_palindrome),
    ("Violet",      t_violet),     ("RSI",        t_rsi),
    ("MACD",        t_macd),       ("Bayesian",   t_bayesian),
    ("Autocorr",    t_autocorr),   ("Entropy",    t_entropy),
    ("HotNum",      t_hot_num),    ("ColdNum",    t_cold_num),
    ("Window3",     t_window3),    ("DblFlip",    t_double_flip),
    ("Sector",      t_sector),     ("LongTrend",  t_long_trend),
    ("Vol",         t_vol),
]

# ---- Parametric weighted trend ----
def make_wt(decay, min_len):
    def wt(seq, _d=decay, _m=min_len):
        if len(seq)<_m: return None
        w=[_d**i for i in range(len(seq))]
        tw=sum(w)
        big=sum(w[i] for i in range(len(seq)) if is_big(seq[i]))
        small=tw-big
        b="Big" if big>small else "Small"
        return {"size":b,"conf":min(94,max(52,(max(big,small)/tw)*100))}
    return wt

# ---- Markov chains ----
def make_markov(depth):
    def markov(seq, _d=depth):
        if len(seq)<_d+2: return None
        patterns={}
        for i in range(_d, min(3000,len(seq))-1):
            key=tuple(get_class(seq[i-j]) for j in range(_d))
            patterns.setdefault(key,[]).append(get_class(seq[i]))
        key=tuple(get_class(seq[j]) for j in range(_d))
        if key in patterns and len(patterns[key])>=3:
            cnt=Counter(patterns[key]).most_common(1)[0]
            return {"size":cnt[0],"conf":min(88,(cnt[1]/len(patterns[key]))*100)}
    return markov

# ---- Build ALL_MODELS ----
ALL_MODELS = [{"name": n, "fn": f} for n, f in CORE_TRICKS]

for decay in [0.80,0.82,0.85,0.88,0.91,0.93,0.95,0.97,0.99]:
    for mlen in [8,12,15,20,25,30,35,40,50,60]:
        ALL_MODELS.append({"name":f"WT_{decay}_{mlen}","fn":make_wt(decay,mlen)})

for depth in [2,3,4,5,6,7]:
    ALL_MODELS.append({"name":f"Markov{depth}","fn":make_markov(depth)})

random.seed(42)   # reproducible variant names
for i in range(700):
    d=0.79+random.random()*0.20
    m=7+random.randint(0,53)
    ALL_MODELS.append({"name":f"Var_{i}","fn":make_wt(d,m)})

print(f"✅ Total models: {len(ALL_MODELS)}")
MODEL_NAMES = [m["name"] for m in ALL_MODELS]

# ==================== BACKTEST-BASED WEIGHT INIT ====================
def backtest_weights(seq, weights):
    """Run backtest on last 200 games to warm-up weights before live trading."""
    if len(seq) < 60:
        return weights
    print(f"🔬 Backtesting {len(seq)} games to calibrate {len(ALL_MODELS)} models...")
    win_map  = defaultdict(int)
    tot_map  = defaultdict(int)
    for i in range(10, min(200, len(seq)-1)):
        window = seq[i:]        # everything older than position i
        actual = get_class(seq[i-1])
        for m in ALL_MODELS:
            try:
                res = m["fn"](window)
                if res and res["conf"] >= 48:
                    tot_map[m["name"]] += 1
                    if res["size"] == actual:
                        win_map[m["name"]] += 1
            except: pass

    for name in MODEL_NAMES:
        t = tot_map[name]
        if t >= 5:
            acc = win_map[name] / t
            weights[name]["wins"]   = win_map[name]
            weights[name]["total"]  = t
            weights[name]["weight"] = max(0.3, min(3.5, 0.4 + acc * 3.1))

    save_weights(weights)
    top = sorted([(n, weights[n]["weight"]) for n in MODEL_NAMES], key=lambda x: -x[1])[:5]
    print(f"✅ Backtest done. Top models: {top}")
    return weights

# ==================== ENSEMBLE ====================
def ensemble_predict(seq, weights):
    votes = {"Big": 0.0, "Small": 0.0}
    total_w = 0.0
    vote_map = {}
    active = 0

    for m in ALL_MODELS:
        try:
            res = m["fn"](seq)
            if res and res["conf"] >= 48:
                w = weights[m["name"]]["weight"] * (res["conf"] / 100.0)
                votes[res["size"]] += w
                total_w += w
                vote_map[m["name"]] = res["size"]
                active += 1
            else:
                vote_map[m["name"]] = None
        except:
            vote_map[m["name"]] = None

    if total_w == 0:
        big = sum(1 for n in seq[:20] if is_big(n))
        small = min(20, len(seq)) - big
        best = "Big" if big > small else "Small"
        return best, min(82, 55 + abs(big-small)*2), active, vote_map

    best = "Big" if votes["Big"] > votes["Small"] else "Small"
    raw  = votes[best] / total_w
    conf = round(min(96, max(52, raw * 100)), 1)
    return best, conf, active, vote_map

def predict_number(seq, pred_size):
    class_nums = [n for n in seq if get_class(n) == pred_size]
    if len(class_nums) < 5:
        return random.choice([5,6,7,8,9] if pred_size=="Big" else [0,1,2,3,4])

    freq = Counter(class_nums)
    comb = {}
    target_range = range(5,10) if pred_size=="Big" else range(5)
    total_w = 0
    for idx, n in enumerate(class_nums):
        w = 0.96 ** idx
        comb[n] = comb.get(n, 0) + w
        total_w += w

    # zero out wrong side
    for n in list(comb):
        if (pred_size=="Big" and n<5) or (pred_size=="Small" and n>=5):
            del comb[n]

    # boost violets (0 and 5) slightly
    if 0 in comb: comb[0] *= 1.3
    if 5 in comb: comb[5] *= 1.3

    total = sum(comb.values()) or 1
    r = random.random(); cum = 0
    for n, v in sorted(comb.items(), key=lambda x: -x[1]):
        cum += v / total
        if r <= cum: return n
    return list(target_range)[0]

# ==================== GLOBAL STATE ====================
init_db()
weights = {}
last_pred_period = None

# ==================== AUTO LOOP ====================
async def auto_loop(app):
    global weights, last_pred_period
    await asyncio.sleep(5)
    print("🤖 Auto-loop started")

    while True:
        try:
            data = fetch_latest(150)
            if not data:
                await asyncio.sleep(AUTO_SEC)
                continue

            # Log history
            for item in reversed(data):
                log_history(item["period"], item["number"])

            current = data[0]
            cp = current["period"]
            cn = current["number"]

            # Settle pending predictions
            for period in get_pending():
                if period == cp: continue
                actual = get_history_num(period)
                if actual is None: continue
                result = settle_prediction(period, actual)
                if not result: continue

                info = get_pred_info(period)
                if not info: continue
                pred_s, pred_n, act_s, act_n, conf, _ = info

                # Update streak stats
                seq = [item["number"] for item in data]
                update_streak_stats(seq, act_s)

                emoji = "✅" if result == "WIN" else "❌"
                total, wins, losses, acc = get_stats()

                settle_msg = (
                    f"{emoji} *RESULT SETTLED — #{period}*\n"
                    f"Predicted: *{pred_s}* (#{pred_n}) | Actual: *{act_s}* (#{act_n})\n"
                    f"Result: *{result}* | Confidence was: {conf:.0f}%\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 Overall: {wins}W / {losses}L = *{acc}%* accuracy"
                )
                if CHAT_ID:
                    try: await app.bot.send_message(chat_id=CHAT_ID, text=settle_msg, parse_mode="Markdown")
                    except Exception as e: print(f"Settle msg error: {e}")

            # New prediction for current period
            if cp != last_pred_period:
                last_pred_period = cp
                seq = [item["number"] for item in data]
                pred_size, conf, active, vote_map = ensemble_predict(seq, weights)
                pred_num   = predict_number(seq, pred_size)
                pred_color = get_color(pred_num)
                save_prediction(cp, pred_num, pred_size, pred_color, conf, active)

                # Probability breakdown
                big_votes  = sum(1 for v in vote_map.values() if v=="Big")
                small_votes = sum(1 for v in vote_map.values() if v=="Small")
                total_votes = big_votes + small_votes or 1
                big_pct    = round(big_votes/total_votes*100)
                small_pct  = 100 - big_pct

                size_e = "🟢 BIG" if pred_size=="Big" else "🔵 SMALL"
                ce     = {"red":"🔴","green":"🟢","violet":"🟣"}.get(pred_color,"⚪")
                total, wins, losses, acc = get_stats()

                # current streak
                streak=1; lc=get_class(seq[0])
                for n in seq[1:]:
                    if get_class(n)==lc: streak+=1
                    else: break
                rev_p = round(get_reversal_prob(streak)*100)

                msg = (
                    f"🧠 *WINGO AI — PERIOD {cp}*\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"🎯 Number: *{pred_num}* {ce} {pred_color.upper()}\n"
                    f"📊 Size: *{size_e}*\n"
                    f"🔬 Confidence: *{conf:.0f}%*\n"
                    f"📈 Votes: Big {big_pct}% | Small {small_pct}%\n"
                    f"🤖 Active models: {active}/{len(ALL_MODELS)}\n"
                    f"🔄 Streak: {streak}x {lc} → reversal P={rev_p}%\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"📋 Record: {wins}W/{losses}L | Acc: {acc}%\n"
                    f"⏱ Auto-predicts every {AUTO_SEC}s"
                )
                if CHAT_ID:
                    try: await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
                    except Exception as e: print(f"Pred send error: {e}")
                print(f"🎯 Period {cp}: {pred_size} #{pred_num} ({conf:.0f}%) | {active} models active")

        except Exception as e:
            print(f"Auto-loop error: {e}")

        await asyncio.sleep(AUTO_SEC)

# ==================== COMMANDS ====================
async def cmd_start(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    total, wins, losses, acc = get_stats()
    await upd.message.reply_text(
        f"👑 *WINGO MONSTER AI BOT*\n"
        f"🤖 {len(ALL_MODELS)} models | Streak-learning | Adaptive weights\n\n"
        f"📊 Stats: {wins}W / {losses}L | Accuracy: {acc}%\n\n"
        f"*Commands:*\n"
        f"/predict — instant prediction now\n"
        f"/stats — full accuracy & top models\n"
        f"/history — last 20 results\n"
        f"/accuracy — settled predictions detail\n"
        f"/streak — current streak analysis\n"
        f"/setcid — enable auto-predictions in this chat\n\n"
        f"💡 Send /setcid once to get automatic predictions!",
        parse_mode="Markdown")

async def cmd_predict(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await upd.message.reply_text("📊 Fetching live data & running ensemble...")
    data = fetch_latest(150)
    if not data or len(data) < 15:
        await upd.message.reply_text("⚠️ API unavailable. Try again in a moment."); return

    for item in reversed(data):
        log_history(item["period"], item["number"])

    cp = data[0]["period"]
    seq = [item["number"] for item in data]
    pred_size, conf, active, vote_map = ensemble_predict(seq, weights)
    pred_num   = predict_number(seq, pred_size)
    pred_color = get_color(pred_num)
    save_prediction(cp, pred_num, pred_size, pred_color, conf, active)
    global last_pred_period; last_pred_period = cp

    big_votes   = sum(1 for v in vote_map.values() if v=="Big")
    small_votes = sum(1 for v in vote_map.values() if v=="Small")
    tvotes = big_votes+small_votes or 1
    big_pct = round(big_votes/tvotes*100)

    streak=1; lc=get_class(seq[0])
    for n in seq[1:]:
        if get_class(n)==lc: streak+=1
        else: break
    rev_p=round(get_reversal_prob(streak)*100)

    last10="".join("B" if is_big(n) else "S" for n in seq[:10])
    size_e = "🟢 BIG" if pred_size=="Big" else "🔵 SMALL"
    ce={"red":"🔴","green":"🟢","violet":"🟣"}.get(pred_color,"⚪")
    total, wins, losses, acc = get_stats()

    await upd.message.reply_text(
        f"🧠 *PREDICTION — PERIOD {cp}*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Number: *{pred_num}* {ce} {pred_color.upper()}\n"
        f"📊 Size: *{size_e}*\n"
        f"🔬 Confidence: *{conf:.0f}%*\n"
        f"📈 Votes: Big {big_pct}% | Small {100-big_pct}%\n"
        f"🤖 Models active: {active}/{len(ALL_MODELS)}\n"
        f"🔄 Streak: {streak}x {lc} | Reversal P={rev_p}%\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Last 10: `{last10}`\n"
        f"📈 Record: {wins}W/{losses}L | Acc: {acc}%\n\n"
        f"✅ Result auto-settles next period!",
        parse_mode="Markdown")

async def cmd_stats(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    total, wins, losses, acc = get_stats()
    conn = db(); c = conn.cursor()
    c.execute("SELECT name,weight,wins,total FROM model_weights WHERE total>8 ORDER BY weight DESC LIMIT 10")
    top = c.fetchall(); conn.close()

    ml = ""
    for name, weight, mw, mt in top:
        macc = round(mw/mt*100) if mt else 0
        ml += f"• `{name[:16]}` {macc}% ({mw}/{mt}) w={weight:.2f}\n"

    # streak stats summary
    ss = load_streak_stats()
    sl = ""
    for L in range(1,7):
        d=ss.get(L,{"same":0,"opp":0})
        t=d["same"]+d["opp"]
        if t>0: sl+=f"Streak-{L}: rev {round(d['opp']/t*100)}% ({t} obs)\n"

    await upd.message.reply_text(
        f"📊 *BOT STATISTICS*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 Total: {total} | ✅ {wins}W | ❌ {losses}L\n"
        f"🎯 Accuracy: *{acc}%*\n"
        f"🤖 Models: {len(ALL_MODELS)}\n\n"
        f"*Top 10 Models:*\n{ml or 'Collecting data...'}\n"
        f"*Streak Reversal Stats:*\n{sl or 'Collecting data...'}",
        parse_mode="Markdown")

async def cmd_history(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    conn = db(); c = conn.cursor()
    c.execute("SELECT period,number,size,color FROM history ORDER BY ts DESC LIMIT 20")
    rows = c.fetchall(); conn.close()
    if not rows:
        await upd.message.reply_text("No history yet."); return
    ce_map={"red":"🔴","green":"🟢","violet":"🟣"}
    lines=[f"`{p[-6:]}` {ce_map.get(col,'⚪')} *{n}* {'▲' if s=='Big' else '▼'}{s}" for p,n,s,col in rows]
    big=sum(1 for _,_,s,_ in rows if s=="Big")
    await upd.message.reply_text(
        f"📜 *RECENT 20 RESULTS*\n━━━━━━━━━━━━━━━━━━━\n"
        +"\n".join(lines)+
        f"\n━━━━━━━━━━━━━━━━━━━\nBig: {big} | Small: {len(rows)-big}",
        parse_mode="Markdown")

async def cmd_accuracy(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    conn = db(); c = conn.cursor()
    c.execute("SELECT period,pred_size,actual_size,pred_num,actual_num,result,confidence FROM predictions WHERE result IS NOT NULL ORDER BY ts DESC LIMIT 15")
    rows = c.fetchall(); conn.close()
    if not rows:
        await upd.message.reply_text("No settled predictions yet."); return
    lines=[]
    for p,ps,as_,pn,an,res,conf in rows:
        re="✅" if res=="WIN" else "❌"
        lines.append(f"{re} `{p[-6:]}` P:{ps}#{pn}→A:{as_}#{an} ({conf:.0f}%)")
    total, wins, losses, acc = get_stats()
    results=[r[5] for r in rows]
    streak=0
    for r in results:
        if r==results[0]: streak+=1
        else: break
    se="🔥" if results[0]=="WIN" else "💀"
    await upd.message.reply_text(
        f"🎯 *ACCURACY REPORT*\n━━━━━━━━━━━━━━━━━━━\n"
        f"Overall: {wins}W/{losses}L = *{acc}%*\n"
        f"Streak: {se} {streak}x {results[0]}\n\n"
        +"\n".join(lines), parse_mode="Markdown")

async def cmd_streak(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = fetch_latest(50)
    if not data:
        await upd.message.reply_text("Could not fetch data."); return
    seq=[item["number"] for item in data]
    streak=1; lc=get_class(seq[0])
    for n in seq[1:]:
        if get_class(n)==lc: streak+=1
        else: break
    rev_p=get_reversal_prob(streak)
    ss=load_streak_stats()
    lines=""
    for L in range(1,7):
        d=ss.get(L,{"same":0,"opp":0})
        t=d["same"]+d["opp"]
        if t>0:
            rp=round(d["opp"]/t*100)
            bar="█"*int(rp/10)+"░"*(10-int(rp/10))
            lines+=f"Streak-{L}: {bar} {rp}% rev ({t} obs)\n"
    await upd.message.reply_text(
        f"🔄 *STREAK ANALYSIS*\n━━━━━━━━━━━━━━━━━━━\n"
        f"Current: *{streak}x {lc}*\n"
        f"Reversal Probability: *{round(rev_p*100)}%*\n\n"
        f"*Learned Reversal Rates:*\n`{lines or 'Still learning...'}`\n\n"
        f"Recent 20: `{''.join('B' if is_big(n) else 'S' for n in seq[:20])}`",
        parse_mode="Markdown")

async def cmd_setcid(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global CHAT_ID
    CHAT_ID = str(upd.effective_chat.id)
    await upd.message.reply_text(
        f"✅ *Auto-predictions enabled!*\n"
        f"Chat ID: `{CHAT_ID}`\n"
        f"Predictions every ~{AUTO_SEC}s | Results auto-settle!",
        parse_mode="Markdown")

# ==================== MAIN ====================
def main():
    global weights
    print(f"👑 WINGO MONSTER BOT — {len(ALL_MODELS)} models")

    print("📊 Fetching initial history...")
    data = fetch_latest(250)
    if data:
        for item in reversed(data): log_history(item["period"], item["number"])
        print(f"✅ Loaded {len(data)} games")
    else:
        print("⚠️ Could not fetch initial data — will retry in auto-loop")

    weights = load_weights(MODEL_NAMES)

    # Backtest to calibrate weights if we have data
    if data and len(data) >= 60:
        seq = [item["number"] for item in data]
        weights = backtest_weights(seq, weights)
    else:
        print("⚠️ Not enough data for backtest, using stored/default weights")

    app = Application.builder().token(BOT_TOKEN).build()
    for cmd, fn in [("start", cmd_start), ("predict", cmd_predict),
                    ("stats", cmd_stats), ("history", cmd_history),
                    ("accuracy", cmd_accuracy), ("streak", cmd_streak),
                    ("setcid", cmd_setcid), ("setchat", cmd_setcid)]:
        app.add_handler(CommandHandler(cmd, fn))

    app.post_init = lambda a: asyncio.ensure_future(auto_loop(a))
    print("🚀 Bot polling started. Send /setcid in your chat to receive auto-predictions.")
    app.run_polling()

if __name__ == "__main__":
    from flask import Flask
    import threading
    web = Flask(__name__)
    @web.route("/")
    def home():
        total, wins, losses, acc = get_stats()
        return f"WinGo Monster AI | {len(ALL_MODELS)} models | {wins}W/{losses}L | Acc:{acc}%"
    def run_web():
        port = int(os.getenv("PORT", 5050))
        web.run(host="0.0.0.0", port=port, use_reloader=False)
    threading.Thread(target=run_web, daemon=True).start()
    main()
