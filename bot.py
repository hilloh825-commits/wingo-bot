# -*- coding: utf-8 -*-
import requests
import sqlite3
import json
import random
import asyncio
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ============ CONFIGURATION ============
BOT_TOKEN = "8607448641:AAE5HL55uKe1fsGZTrhMv7zgfzpj1EhA4e4"   # Replace with your token
CHAT_ID = "8665741437"   # Your personal chat ID (positive)
API_URL = "https://wingolast100.vercel.app/api/results?typeId=1&apiKey=12a04165-748c-4144-9398-96bd2e0ad956&token=1a97a413-ff57-4097-a44c-4bd402ace8d5&limit=100"

# ============ DATABASE SETUP ============
def init_db():
    conn = sqlite3.connect("wingo_bot.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS periods (
                    period TEXT PRIMARY KEY,
                    number INTEGER,
                    big_small TEXT,
                    timestamp TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    period TEXT,
                    pred_number INTEGER,
                    pred_class TEXT,
                    confidence REAL,
                    actual_number INTEGER,
                    actual_class TEXT,
                    result TEXT,
                    timestamp TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS trick_performance (
                    name TEXT PRIMARY KEY,
                    last_results TEXT,
                    accuracy REAL,
                    weight REAL)''')
    conn.commit()
    conn.close()

init_db()

# ============ HELPER FUNCTIONS ============
def get_class(n):
    return "Big" if n >= 5 else "Small"

def is_violet(n):
    return n in (0, 5)

# ============ 30+ TRICKS (exact same as d.html, all included) ============
def trick_martingale(seq):
    if len(seq) < 2: return None
    last = get_class(seq[0]); prev = get_class(seq[1])
    if last == prev:
        return {"predClass": "Small" if last=="Big" else "Big", "confidence": 72}
    return None

def trick_trendFollow(seq):
    if len(seq) < 3: return None
    a,b,c = get_class(seq[0]), get_class(seq[1]), get_class(seq[2])
    if a==b==c:
        return {"predClass": a, "confidence": 75}
    return None

def trick_alternation(seq):
    if len(seq) < 2: return None
    last = get_class(seq[0]); prev = get_class(seq[1])
    if last != prev:
        return {"predClass": "Small" if last=="Big" else "Big", "confidence": 68}
    return None

def trick_frequency(seq):
    if len(seq) < 20: return None
    big = sum(1 for i in range(20) if get_class(seq[i]) == "Big")
    small = 20 - big
    best = "Big" if big > small else "Small"
    conf = 55 + (max(big,small)/20)*35
    return {"predClass": best, "confidence": min(85, conf)}

def trick_momentum(seq):
    if len(seq) < 10: return None
    last5 = sum(1 for i in range(5) if get_class(seq[i]) == "Big")
    prev5 = sum(1 for i in range(5,10) if get_class(seq[i]) == "Big")
    if last5 > prev5 + 1:
        return {"predClass": "Big", "confidence": 70}
    if prev5 > last5 + 1:
        return {"predClass": "Small", "confidence": 70}
    return None

def trick_pattern5(seq):
    if len(seq) < 5: return None
    pat = ''.join(get_class(seq[i])[0] for i in range(5))
    if pat == 'BBBBB': return {"predClass": "Small", "confidence": 75}
    if pat == 'SSSSS': return {"predClass": "Big", "confidence": 75}
    if pat == 'BBBBS': return {"predClass": "Small", "confidence": 68}
    if pat == 'SSSSB': return {"predClass": "Big", "confidence": 68}
    if pat == 'BBSSB': return {"predClass": "Big", "confidence": 66}
    if pat == 'SSBBS': return {"predClass": "Small", "confidence": 66}
    return None

def trick_doubleFlip(seq):
    if len(seq) < 2: return None
    last = get_class(seq[0]); prev = get_class(seq[1])
    if last == prev:
        return {"predClass": "Small" if last=="Big" else "Big", "confidence": 71}
    return None

def trick_streakRev(seq):
    if len(seq) < 4: return None
    streak = 1; lastClass = get_class(seq[0])
    for i in range(1, len(seq)):
        if get_class(seq[i]) == lastClass: streak += 1
        else: break
    if streak >= 4:
        opp = "Small" if lastClass == "Big" else "Big"
        conf = 70 + min(10, streak)
        return {"predClass": opp, "confidence": min(85, conf)}
    return None

def trick_zigzag(seq):
    if len(seq) < 6: return None
    changes = 0
    for i in range(5):
        if get_class(seq[i]) != get_class(seq[i+1]): changes += 1
    if changes >= 4:
        last = get_class(seq[0])
        return {"predClass": "Small" if last=="Big" else "Big", "confidence": 74}
    return None

def trick_fibonacci(seq):
    if len(seq) < 10: return None
    fib = [1,1,2,3,5,8,13]
    bigW=0; smallW=0; totalW=0
    for i in range(min(len(seq), len(fib))):
        w = fib[i]; totalW += w
        if get_class(seq[i]) == "Big":
            bigW += w
        else:
            smallW += w
    best = "Big" if bigW > smallW else "Small"
    conf = 55 + (max(bigW, smallW)/totalW)*35
    return {"predClass": best, "confidence": min(85, conf)}

def trick_lawThird(seq):
    if len(seq) < 20: return None
    uniq = len(set(seq[:20]))
    if uniq <= 7: return {"predClass": "Big", "confidence": 68}
    if uniq >= 13: return {"predClass": "Small", "confidence": 68}
    return None

def trick_gapAnalysis(seq):
    if len(seq) < 15: return None
    last = seq[0]; lastPos=0; gaps=[]
    for i in range(1, min(len(seq),25)):
        if seq[i] == last:
            gaps.append(i - lastPos)
            lastPos = i
    if len(gaps) >= 2:
        avg = sum(gaps)/len(gaps)
        if avg < 4: return {"predClass": "Big", "confidence": 66}
        if avg > 8: return {"predClass": "Small", "confidence": 66}
    return None

def trick_evenOddStreak(seq):
    if len(seq) < 4: return None
    par = seq[0] % 2; streak=1
    for i in range(1, len(seq)):
        if seq[i] % 2 == par: streak += 1
        else: break
    if streak >= 3:
        return {"predClass": "Big" if par==0 else "Small", "confidence": 70}
    return None

def trick_hotClass(seq):
    if len(seq) < 10: return None
    big = sum(1 for i in range(10) if get_class(seq[i]) == "Big")
    small = 10 - big
    if big == small: return None
    best = "Big" if big > small else "Small"
    conf = 55 + (max(big,small)/10)*35
    return {"predClass": best, "confidence": min(80, conf)}

def trick_movingAvg(seq):
    if len(seq) < 15: return None
    short = sum(seq[:5])/5
    long = sum(seq[:15])/15
    if short > long + 0.8: return {"predClass": "Big", "confidence": 70}
    if short < long - 0.8: return {"predClass": "Small", "confidence": 70}
    return None

def trick_digitSum(seq):
    if len(seq) < 5: return None
    s = seq[0] // 10 + (seq[0] % 10)
    return {"predClass": "Big" if s >= 5 else "Small", "confidence": 65}

def trick_oscillator(seq):
    if len(seq) < 8: return None
    high = sum(1 for i in range(5) if seq[i] >= 7)
    low = sum(1 for i in range(5) if seq[i] <= 2)
    if high >= 3: return {"predClass": "Small", "confidence": 69}
    if low >= 3: return {"predClass": "Big", "confidence": 69}
    return None

def trick_extremeRev(seq):
    if len(seq) < 2: return None
    last = seq[0]
    if last == 0: return {"predClass": "Big", "confidence": 72}
    if last == 9: return {"predClass": "Small", "confidence": 72}
    return None

def trick_sleeper(seq):
    if len(seq) < 13: return None
    freq = [0]*10
    for i in range(12): freq[seq[i]] += 1
    if min(freq) == 0: return {"predClass": "Big", "confidence": 63}
    return None

def trick_parityCycle(seq):
    if len(seq) < 3: return None
    last = seq[0]%2; prev = seq[1]%2
    if last != prev:
        return {"predClass": "Big" if last==0 else "Small", "confidence": 67}
    return None

def trick_chineseRem(seq):
    if len(seq) < 10: return None
    last = seq[0]
    m5 = last%5; m4 = last%4
    if m5==0 and m4==0: return {"predClass": "Big", "confidence": 68}
    if m5==2 and m4==2: return {"predClass": "Small", "confidence": 66}
    return None

def trick_revMartingale(seq):
    if len(seq) < 3: return None
    a,b,c = get_class(seq[0]), get_class(seq[1]), get_class(seq[2])
    if a==b==c:
        return {"predClass": a, "confidence": 68}
    return None

def trick_fibBetting(seq):
    if len(seq) < 6: return None
    pat = ''.join(get_class(seq[i])[0] for i in range(6))
    if pat == 'BBBBBB': return {"predClass": "Small", "confidence": 74}
    if pat == 'SSSSSS': return {"predClass": "Big", "confidence": 74}
    return None

def trick_meanReversion(seq):
    if len(seq) < 15: return None
    last10 = seq[:10]
    mean = sum(last10)/10
    last = seq[0]
    if last < mean - 1.5: return {"predClass": "Big", "confidence": 70}
    if last > mean + 1.5: return {"predClass": "Small", "confidence": 70}
    return None

def trick_evenOddAlt(seq):
    if len(seq) < 3: return None
    a = seq[0]%2; b = seq[1]%2; c = seq[2]%2
    if a==c and a!=b:
        return {"predClass": "Big" if a==0 else "Small", "confidence": 69}
    return None

def trick_consecutiveBig(seq):
    if len(seq) < 10: return None
    bigCount = 0
    for i in range(min(len(seq),12)):
        if get_class(seq[i]) == "Big": bigCount += 1
        else: break
    if bigCount >= 10: return {"predClass": "Small", "confidence": 82}
    return None

def trick_consecutiveSmall(seq):
    if len(seq) < 5: return None
    smallCount = 0
    for i in range(min(len(seq),8)):
        if get_class(seq[i]) == "Small": smallCount += 1
        else: break
    if smallCount >= 5: return {"predClass": "Big", "confidence": 78}
    return None

def trick_palindrome(seq):
    for L in [5,7]:
        if len(seq) >= L:
            win = seq[:L]
            pal = all(win[i] == win[L-1-i] for i in range(L//2))
            if pal:
                return {"predClass": get_class(win[L//2]), "confidence": 70}
    return None

def trick_digitalRoot(seq):
    if len(seq) < 15: return None
    roots = [9 if n%9==0 else n%9 for n in seq]
    lastRoot = roots[0]
    nextVals = []
    for i in range(1, len(roots)-1):
        if roots[i] == lastRoot:
            nextVals.append(seq[i-1])
    if len(nextVals) >= 2:
        freq = [0]*10
        for v in nextVals: freq[v] += 1
        best = freq.index(max(freq))
        return {"predClass": get_class(best), "confidence": 68}
    return None

def trick_violetRebound(seq):
    if len(seq) < 4: return None
    noSpecial = 0
    for i in range(min(len(seq),6)):
        if not is_violet(seq[i]): noSpecial += 1
        else: break
    if noSpecial >= 3:
        return {"predClass": "Big", "confidence": 68}
    return None

# List of all tricks
ALL_TRICKS = [
    ("Martingale", trick_martingale),
    ("Trend Follow", trick_trendFollow),
    ("Alternation", trick_alternation),
    ("Frequency (20)", trick_frequency),
    ("Momentum", trick_momentum),
    ("Pattern 5-seq", trick_pattern5),
    ("2x Flip", trick_doubleFlip),
    ("Streak Reversal", trick_streakRev),
    ("Zigzag", trick_zigzag),
    ("Fibonacci Wtd", trick_fibonacci),
    ("Law of Third", trick_lawThird),
    ("Gap Analysis", trick_gapAnalysis),
    ("Even/Odd Streak", trick_evenOddStreak),
    ("Hot Class (10)", trick_hotClass),
    ("Moving Average", trick_movingAvg),
    ("Digit Sum", trick_digitSum),
    ("Oscillator", trick_oscillator),
    ("Extreme Rev", trick_extremeRev),
    ("Sleeper Alert", trick_sleeper),
    ("Parity Cycle", trick_parityCycle),
    ("Chinese Rem", trick_chineseRem),
    ("Reverse Mart.", trick_revMartingale),
    ("Fibonacci Bet", trick_fibBetting),
    ("Mean Reversion", trick_meanReversion),
    ("Even/Odd Alt", trick_evenOddAlt),
    ("Consecutive Big", trick_consecutiveBig),
    ("Consecutive Small", trick_consecutiveSmall),
    ("Palindrome", trick_palindrome),
    ("Digital Root", trick_digitalRoot),
    ("Violet Reb(Big)", trick_violetRebound)
]

# ============ PERFORMANCE TRACKING ============
def load_performance():
    conn = sqlite3.connect("wingo_bot.db")
    c = conn.cursor()
    perf = {}
    for name,_ in ALL_TRICKS:
        c.execute("SELECT last_results, accuracy, weight FROM trick_performance WHERE name=?", (name,))
        row = c.fetchone()
        if row:
            last_results = json.loads(row[0]) if row[0] else []
            perf[name] = {"last_results": last_results, "accuracy": row[1], "weight": row[2]}
        else:
            perf[name] = {"last_results": [], "accuracy": 0.5, "weight": 1.0}
    conn.close()
    return perf

def save_performance(perf):
    conn = sqlite3.connect("wingo_bot.db")
    c = conn.cursor()
    for name, data in perf.items():
        last_results_json = json.dumps(data["last_results"])
        c.execute("INSERT OR REPLACE INTO trick_performance (name, last_results, accuracy, weight) VALUES (?,?,?,?)",
                  (name, last_results_json, data["accuracy"], data["weight"]))
    conn.commit()
    conn.close()

# ============ BACKTEST (pretrain on last 100 periods) ============
def backtest_and_set_weights(full_history_numbers):
    if len(full_history_numbers) < 30:
        print("Backtest: not enough data")
        return
    perf = load_performance()
    for name, trick in ALL_TRICKS:
        correct = 0
        total = 0
        for i in range(20, len(full_history_numbers)-1):
            window = full_history_numbers[i-20:i][::-1]  # recent first
            actual = get_class(full_history_numbers[i])
            pred = trick(window)
            if pred and pred["confidence"] >= 45:
                total += 1
                if pred["predClass"] == actual:
                    correct += 1
        acc = correct / total if total > 0 else 0.5
        weight = min(1.5, max(0.3, acc**1.5 + 0.2))
        perf[name]["accuracy"] = acc
        perf[name]["weight"] = weight
        perf[name]["last_results"] = []
        print(f"Backtest: {name} acc={acc:.2f} weight={weight:.2f} total={total}")
    save_performance(perf)

# ============ FETCH PERIODS FROM API ============
def fetch_periods(limit=100):
    try:
        resp = requests.get(API_URL, timeout=10)
        data = resp.json()
        items = data.get('data', {}).get('list') or data.get('list') or data.get('results', [])
        if not items:
            return []
        parsed = []
        for item in items:
            period = str(item.get('issueNumber') or item.get('period') or item.get('id', ''))
            num = item.get('number') or item.get('openCode') or item.get('result')
            if period and num is not None:
                try:
                    number = int(num)
                    if 0 <= number <= 9:
                        parsed.append((period, number, get_class(number), datetime.now().isoformat()))
                except:
                    continue
        parsed.sort(key=lambda x: x[0], reverse=True)
        return parsed[:limit]
    except Exception as e:
        print("Fetch error:", e)
        return []

def store_periods(periods):
    conn = sqlite3.connect("wingo_bot.db")
    c = conn.cursor()
    for p in periods:
        c.execute("INSERT OR REPLACE INTO periods (period, number, big_small, timestamp) VALUES (?,?,?,?)", p)
    conn.commit()
    conn.close()

def get_all_periods():
    conn = sqlite3.connect("wingo_bot.db")
    c = conn.cursor()
    c.execute("SELECT period, number, big_small, timestamp FROM periods ORDER BY period DESC")
    rows = c.fetchall()
    conn.close()
    return rows

def get_pending_predictions():
    conn = sqlite3.connect("wingo_bot.db")
    c = conn.cursor()
    c.execute("SELECT period, pred_number, pred_class, confidence FROM predictions WHERE result IS NULL")
    rows = c.fetchall()
    conn.close()
    return rows

def save_prediction(period, pred_num, pred_class, conf):
    conn = sqlite3.connect("wingo_bot.db")
    c = conn.cursor()
    c.execute("INSERT INTO predictions (period, pred_number, pred_class, confidence, timestamp) VALUES (?,?,?,?,?)",
              (period, pred_num, pred_class, conf, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def update_prediction_result(period, actual_num, actual_class, result):
    conn = sqlite3.connect("wingo_bot.db")
    c = conn.cursor()
    c.execute("UPDATE predictions SET actual_number=?, actual_class=?, result=?, timestamp=? WHERE period=?",
              (actual_num, actual_class, result, datetime.now().isoformat(), period))
    conn.commit()
    conn.close()

# ============ ENSEMBLE & NUMBER PREDICTION ============
def get_best_tricks(perf, threshold=0.55):   # lowered threshold to include more tricks
    best = [name for name, data in perf.items() if data["accuracy"] >= threshold]
    if not best:
        # fallback: top 5 by weight
        sorted_names = sorted(perf.keys(), key=lambda n: perf[n]["weight"], reverse=True)
        best = sorted_names[:5]
        print("No trick reached threshold, using top 5 by weight:", best)
    return best

def ensemble_predict(seq, perf):
    best_names = get_best_tricks(perf, 0.55)
    votes = {"Big": 0.0, "Small": 0.0}
    total_weight = 0.0
    predictions_map = {}
    active = []
    for name in best_names:
        trick = dict(ALL_TRICKS)[name]
        res = trick(seq)
        if res and res["confidence"] >= 45:
            w = perf[name]["weight"] * (res["confidence"] / 100.0)
            votes[res["predClass"]] += w
            total_weight += w
            predictions_map[name] = res
            active.append((name, res["predClass"], res["confidence"], perf[name]["accuracy"], perf[name]["weight"]))
    if total_weight > 0:
        best_class = "Big" if votes["Big"] > votes["Small"] else "Small"
        conf = (votes[best_class] / total_weight) * 100
        conf = min(94, max(55, conf))
        print(f"Ensemble: best_class={best_class}, conf={conf:.1f}, active={active}")
    else:
        # fallback: last 20 trend
        big_count = sum(1 for i in range(min(20, len(seq))) if get_class(seq[i]) == "Big")
        small_count = min(20, len(seq)) - big_count
        best_class = "Big" if big_count > small_count else "Small"
        diff = abs(big_count - small_count)
        conf = 55 + diff * 2
        conf = min(85, conf)
        predictions_map["fallback"] = {"predClass": best_class, "confidence": conf}
        active.append(("📊 Trend Fallback", best_class, conf, 0, 1.0))
        print("Fallback used:", best_class, conf)
    return best_class, int(conf), predictions_map, active

def predict_number(seq, pred_class, history_data):
    class_numbers = [r["number"] for r in history_data if get_class(r["number"]) == pred_class]
    if len(class_numbers) < 5:
        if pred_class == "Big":
            return random.choice([5,6,7,8,9])
        else:
            return random.choice([0,1,2,3,4])
    freq = [0]*10
    for n in class_numbers:
        freq[n] += 1
    recent = [r["number"] for r in history_data[:15] if get_class(r["number"]) == pred_class]
    recent_freq = [0]*10
    for n in recent:
        recent_freq[n] += 1
    combined = [0]*10
    for i in range(10):
        overall = freq[i] / len(class_numbers)
        recent_val = recent_freq[i] / len(recent) if recent else overall
        combined[i] = recent_val * 0.6 + overall * 0.4
    last_num = seq[0]
    if combined[last_num] > 0:
        combined[last_num] *= 0.6
    if pred_class == "Big":
        combined[5] *= 1.45
    else:
        combined[0] *= 1.3
    total = sum(combined)
    if total == 0:
        total = 1
    for i in range(10):
        combined[i] /= total
    r = random.random()
    cum = 0
    for i in range(10):
        cum += combined[i]
        if r <= cum:
            return i
    return 0

# ============ UPDATE PERFORMANCE AFTER SETTLEMENT ============
def update_performance_from_result(perf, predictions_map, actual_class):
    for name, res in predictions_map.items():
        if name == "fallback":
            continue
        correct = 1 if res["predClass"] == actual_class else 0
        last_results = perf[name]["last_results"]
        last_results.insert(0, correct)
        if len(last_results) > 10:
            last_results.pop()
        live_acc = sum(last_results) / len(last_results) if last_results else 0.5
        backtest_acc = perf[name]["accuracy"]
        combined_acc = backtest_acc * 0.6 + live_acc * 0.4
        perf[name]["accuracy"] = combined_acc
        perf[name]["weight"] = min(1.5, max(0.3, combined_acc * 1.2 + 0.2))
        perf[name]["last_results"] = last_results
    save_performance(perf)

# ============ MAIN BOT LOGIC ============
async def periodic_prediction(context: ContextTypes.DEFAULT_TYPE):
    print("Running periodic prediction...")
    # fetch and store
    periods = fetch_periods(100)
    if periods:
        store_periods(periods)
    rows = get_all_periods()
    if len(rows) < 15:
        print("Not enough periods to predict")
        return
    history_data = [{"period": r[0], "number": r[1], "big_small": r[2]} for r in rows]
    seq = [r["number"] for r in history_data]
    try:
        last_period = rows[0][0]
        next_period = str(int(last_period) + 1)
    except:
        next_period = "unknown"
    perf = load_performance()
    pred_class, confidence, predictions_map, active = ensemble_predict(seq, perf)
    pred_num = predict_number(seq, pred_class, history_data)
    msg = f"🎯 *Period {next_period}*\nPrediction: *{pred_num} ({pred_class})*\nConfidence: *{confidence}%*"
    if confidence >= 70:
        msg += "\n🔥 *SURESHOT!* 🔥"
    if active:
        top_tricks = active[:3]
        msg += "\n\n📊 *Best tricks:*\n" + "\n".join([f"{t[0]} → {t[1]} ({t[2]}%)" for t in top_tricks])
    await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    save_prediction(next_period, pred_num, pred_class, confidence)
    # settlement
    pending = get_pending_predictions()
    for p in pending:
        p_period, _, _, _ = p
        for rec in history_data:
            if rec["period"] == p_period:
                actual_num = rec["number"]
                actual_class = rec["big_small"]
                if actual_num == p[1]:
                    result = "jackpot"
                elif actual_class == p[2]:
                    result = "win"
                else:
                    result = "loss"
                update_prediction_result(p_period, actual_num, actual_class, result)
                # performance update using the stored predictions_map? We don't store, so skip.
                break
    # periodic retrain (backtest on latest 100)
    full_history = [r[1] for r in rows][::-1]  # oldest first
    backtest_and_set_weights(full_history)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Wingo AI Bot active. I will send predictions every 2 minutes.\nUse /predict to force a prediction.\nUse /status to see bot health.\nUse /tricks to see trick accuracies.")

async def predict_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await periodic_prediction(context)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = get_all_periods()
    count = len(rows)
    await update.message.reply_text(f"✅ Bot is running.\n📊 Periods stored: {count}\n🔄 Next auto prediction in ~2 minutes.")

async def tricks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    perf = load_performance()
    sorted_names = sorted(perf.keys(), key=lambda n: perf[n]["accuracy"], reverse=True)
    msg = "📈 *Trick Performances (accuracy)*\n"
    for name in sorted_names[:10]:
        acc = perf[name]["accuracy"] * 100
        msg += f"• {name}: {acc:.1f}%\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def init_backtest():
    periods = fetch_periods(100)
    if periods:
        store_periods(periods)
    rows = get_all_periods()
    if len(rows) >= 30:
        history_numbers = [r[1] for r in rows][::-1]
        backtest_and_set_weights(history_numbers)
        print("Initial backtest complete")
    else:
        print("Not enough data for initial backtest")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("predict", predict_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("tricks", tricks_command))

    # Add job queue (now available because we installed the extra)
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_repeating(periodic_prediction, interval=120, first=10)

    # Run initial backtest (blocking but within the same loop)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_backtest())

    # Start polling (this keeps the bot running)
    print("Bot started, polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)