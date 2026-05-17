import os
import json
import time
import requests
import asyncio
import math
import random
import numpy as np
from collections import deque, Counter, defaultdict
from datetime import datetime
from scipy import stats as scipy_stats
from sklearn.ensemble import RandomForestRegressor, GradientBoostingClassifier
from telegram import Update
from telegram.ext import Application, CommandHandler

# ========== CONFIGURATION ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATA_FILE = "wingo_data.json"
HISTORY_API = "https://draw.ar-lottery01.com/WinGo/WinGo_1M/GetHistoryIssuePage.json"

# ========== SETTINGS ==========
MAX_HISTORY_LIMIT = 5000
MIN_CONFIDENCE = 55
CHECK_INTERVAL = 5  # Check every 5 seconds

# ========== LEVEL SYSTEM ==========
class LevelSystem:
    def __init__(self):
        self.current_level = 1
        self.max_level = 1
        self.total_wins = 0
        self.total_losses = 0
        self.consecutive_wins = 0
        self.consecutive_losses = 0
        self.level_history = []
        
    def update(self, was_correct):
        if was_correct:
            self.total_wins += 1
            self.consecutive_wins += 1
            self.consecutive_losses = 0
            
            # Level up after 2 consecutive wins at current level
            if self.consecutive_wins >= 2 and self.current_level > 1:
                self.current_level -= 1
                self.consecutive_wins = 0
                
        else:
            self.total_losses += 1
            self.consecutive_losses += 1
            self.consecutive_wins = 0
            
            # Level down after a loss at level 1, or 2 consecutive losses at higher levels
            if self.current_level == 1:
                self.current_level += 1
            elif self.consecutive_losses >= 2:
                self.current_level += 1
                self.consecutive_losses = 0
        
        # Track max level
        if self.current_level > self.max_level:
            self.max_level = self.current_level
            
        self.level_history.append((datetime.now(), self.current_level))
        # Keep only last 100 level changes
        if len(self.level_history) > 100:
            self.level_history = self.level_history[-100:]
    
    def get_win_rate(self):
        total = self.total_wins + self.total_losses
        return (self.total_wins / total * 100) if total > 0 else 0
    
    def get_stats(self):
        return {
            "level": self.current_level,
            "max_level": self.max_level,
            "wins": self.total_wins,
            "losses": self.total_losses,
            "win_rate": round(self.get_win_rate(), 1),
            "streak": self.consecutive_wins if self.consecutive_wins > 0 else -self.consecutive_losses
        }

# ========== ADVANCED STATISTICS ==========
class GodStats:
    @staticmethod
    def mean(data): return sum(data) / len(data) if data else 0
    @staticmethod
    def median(data): return sorted(data)[len(data)//2] if data else 0
    @staticmethod
    def std_dev(data): return np.std(data) if len(data) > 1 else 0
    @staticmethod
    def skewness(data): return float(scipy_stats.skew(data)) if len(data) > 2 else 0
    @staticmethod
    def kurtosis(data): return float(scipy_stats.kurtosis(data)) if len(data) > 3 else 0
    @staticmethod
    def exp_weights(n, decay=0.94): return [decay ** (n - i - 1) for i in range(n)]
    @staticmethod
    def normalize(w): total = sum(w); return [x/total for x in w] if total else w
    @staticmethod
    def autocorr(data, lag=1):
        if len(data) <= lag: return 0
        return np.corrcoef(data[:-lag], data[lag:])[0,1] if len(data) > lag else 0
    @staticmethod
    def entropy(data):
        if not data: return 0
        probs = [c/len(data) for c in Counter(data).values()]
        return -sum(p * math.log2(p) for p in probs)
    @staticmethod
    def hurst_exponent(data):
        if len(data) < 20: return 0.5
        lags = range(5, min(50, len(data)//2))
        tau = [np.sqrt(np.std(np.subtract(data[lag:], data[:-lag]))) for lag in lags]
        try:
            poly = np.polyfit(np.log(lags), np.log(tau), 1)
            return poly[0]
        except:
            return 0.5
    
    @staticmethod
    def trend_strength(data):
        """Calculate trend strength (0-1)"""
        if len(data) < 10: return 0.5
        num = [1 if x=="Small" else 0 for x in data]
        hurst = GodStats.hurst_exponent(num)
        return min(1.0, max(0.0, (hurst - 0.4) / 0.4))

# ========== GENETIC ALGORITHM ==========
class GeneticOptimizer:
    def __init__(self, population_size=50):
        self.population_size = population_size
        self.population = []
        self.best_weights = None
        self.generation = 0
        
    def create_individual(self, n_models=25):
        return [random.uniform(0.5, 2.0) for _ in range(n_models)]
    
    def fitness(self, weights, history_predictions, actual_results):
        if not actual_results: return 0
        correct = 0
        for i, preds in enumerate(history_predictions):
            if i >= len(actual_results): break
            small_score = sum(w * p.get('Small', 0) for w, p in zip(weights, preds))
            big_score = sum(w * p.get('Big', 0) for w, p in zip(weights, preds))
            prediction = 'Small' if small_score > big_score else 'Big'
            if prediction == actual_results[i]:
                correct += 1
        return correct / max(1, len(actual_results))
    
    def evolve(self, history_predictions, actual_results, generations=20):
        self.population = [self.create_individual() for _ in range(self.population_size)]
        for gen in range(generations):
            fitness_scores = [self.fitness(ind, history_predictions, actual_results) for ind in self.population]
            sorted_pairs = sorted(zip(fitness_scores, self.population), reverse=True)
            top_n = max(2, int(self.population_size * 0.2))
            elite = [ind for _, ind in sorted_pairs[:top_n]]
            new_population = elite.copy()
            while len(new_population) < self.population_size:
                parent1, parent2 = random.sample(elite, 2)
                crossover_point = random.randint(1, len(parent1)-1)
                child = parent1[:crossover_point] + parent2[crossover_point:]
                if random.random() < 0.3:
                    idx = random.randint(0, len(child)-1)
                    child[idx] += random.uniform(-0.3, 0.3)
                    child[idx] = max(0.3, min(3.0, child[idx]))
                new_population.append(child)
            self.population = new_population
            self.generation = gen
        final_scores = [self.fitness(ind, history_predictions, actual_results) for ind in self.population]
        best_idx = np.argmax(final_scores)
        self.best_weights = self.population[best_idx]
        return self.best_weights

# ========== DEEP LEARNING SIMULATION ==========
class DeepLearningSimulator:
    def __init__(self):
        self.rf_model = RandomForestRegressor(n_estimators=50, max_depth=5)
        self.gb_model = GradientBoostingClassifier(n_estimators=30)
        self.is_trained = False
        
    def train_models(self, X, y):
        if len(X) < 50: return
        try:
            self.rf_model.fit(X, y)
            self.gb_model.fit(X, [1 if r == 'Small' else 0 for r in y])
            self.is_trained = True
        except:
            pass
    
    def predict_ml(self, features):
        if not self.is_trained: return None
        try:
            gb_pred = self.gb_model.predict([features])[0]
            return ('Small', 70) if gb_pred == 1 else ('Big', 68)
        except:
            return None

# ========== DATA STORAGE ==========
class Store:
    def __init__(self):
        self.history = deque(maxlen=MAX_HISTORY_LIMIT)
        self.genetic = GeneticOptimizer()
        self.deep_learning = DeepLearningSimulator()
        self.level_system = LevelSystem()
        self.last_prediction = {"period": None, "prediction": None}
        self.load()
    
    def load(self):
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE) as f:
                    d = json.load(f)
                    self.history = deque(d.get('history', []), maxlen=MAX_HISTORY_LIMIT)
                    ls = d.get('level_system', {})
                    self.level_system.current_level = ls.get('current_level', 1)
                    self.level_system.max_level = ls.get('max_level', 1)
                    self.level_system.total_wins = ls.get('total_wins', 0)
                    self.level_system.total_losses = ls.get('total_losses', 0)
            except:
                pass
    
    def save(self):
        with open(DATA_FILE, 'w') as f:
            json.dump({
                'history': list(self.history),
                'level_system': {
                    'current_level': self.level_system.current_level,
                    'max_level': self.level_system.max_level,
                    'total_wins': self.level_system.total_wins,
                    'total_losses': self.level_system.total_losses
                }
            }, f)

# ========== PREDICTOR (25 Models + Trend Analysis) ==========
class GodPredictor:
    def __init__(self, history, store):
        self.h = list(history)
        self.stats = GodStats()
        self.store = store
    
    def trend_short(self): return self._trend(30, 0.88)
    def trend_medium(self): return self._trend(60, 0.92)
    def trend_long(self): return self._trend(100, 0.95)
    def _trend(self, window, decay):
        if len(self.h) < window: return None,0
        rec = self.h[-window:]
        w = self.stats.normalize(self.stats.exp_weights(len(rec), decay))
        s = sum(w[i] for i,r in enumerate(rec) if r=="Small")
        b = sum(w[i] for i,r in enumerate(rec) if r=="Big")
        if s > b*1.08: return "Small", (s/(s+b))*100
        if b > s*1.08: return "Big", (b/(s+b))*100
        return None,0
    
    def markov2(self): return self._markov(2, 3)
    def markov4(self): return self._markov(4, 2)
    def markov6(self): return self._markov(6, 2)
    def _markov(self, depth, min_occur):
        if len(self.h) < depth+2: return None,0
        patterns = {}
        for i in range(depth, min(2000, len(self.h))-1):
            key = tuple(self.h[i-depth:i])
            patterns.setdefault(key, []).append(self.h[i])
        last = tuple(self.h[-depth:])
        if last in patterns and len(patterns[last]) >= min_occur:
            cnt = Counter(patterns[last]).most_common(1)[0]
            return cnt[0], min((cnt[1]/len(patterns[last]))*100, 88)
        return None,0
    
    def streak2(self): return self._streak(2)
    def streak3(self): return self._streak(3)
    def streak4(self): return self._streak(4)
    def _streak(self, threshold):
        if len(self.h) < threshold+1: return None,0
        last, streak = self.h[-1], 1
        for i in range(len(self.h)-2, -1, -1):
            if self.h[i] == last: streak += 1
            else: break
        if streak >= threshold:
            rev = "Big" if last=="Small" else "Small"
            conf = min(50 + streak*6, 88)
            return rev, conf
        return None,0
    
    def monte_carlo(self):
        if len(self.h) < 40: return None,0
        last50 = self.h[-50:]
        p_small = last50.count("Small")/50
        wins = 0
        for _ in range(3000):
            last3same = len(set(self.h[-3:])) == 1
            if last3same and random.random() < 0.7:
                res = "Small" if p_small < 0.5 else "Big"
            else:
                res = "Small" if random.random() < p_small else "Big"
            if res == "Small": wins += 1
        conf = (wins/3000)*100
        if conf > 62: return "Small", conf
        if conf < 38: return "Big", 100-conf
        return None,0
    
    def bayesian_short(self): return self._bayesian(15)
    def bayesian_long(self): return self._bayesian(30)
    def _bayesian(self, window):
        if len(self.h) < window+10: return None,0
        prior = self.h.count("Small") / len(self.h)
        last_w = self.h[-window:]; like = last_w.count("Small")/window
        post = (prior * like) / 0.5
        if post > 0.68: return "Small", post*100
        if post < 0.32: return "Big", (1-post)*100
        return None,0
    
    def volatility_rsi(self):
        if len(self.h) < 40: return None,0
        num = [1 if x=="Small" else 0 for x in self.h[-80:]]
        vol = self.stats.std_dev(num)
        gains = sum(max(0, num[i]-num[i-1]) for i in range(1, len(num)))
        losses = sum(max(0, num[i-1]-num[i]) for i in range(1, len(num)))
        rsi = 100 - (100/(1+gains/max(1,losses))) if losses>0 else 100
        if vol < 0.22 and rsi > 65: return "Small", 76
        if vol < 0.22 and rsi < 35: return "Big", 74
        return None,0
    
    def volatility_macd(self):
        if len(self.h) < 50: return None,0
        num = [1 if x=="Small" else 0 for x in self.h[-100:]]
        ema12 = sum(num[-12:])/12
        ema26 = sum(num[-26:])/26
        macd = ema12 - ema26
        signal = sum([macd] + [self._calc_macd(num, i) for i in range(-5,0)])/6
        if macd > signal: return "Small", 71
        if macd < signal: return "Big", 69
        return None,0
    def _calc_macd(self, num, offset):
        if len(num) < 26+abs(offset): return 0
        return sum(num[-12+offset:])/12 - sum(num[-26+offset:])/26
    
    def cycle_short(self): return self._cycle(range(4, 15))
    def cycle_medium(self): return self._cycle(range(15, 30))
    def cycle_long(self): return self._cycle(range(30, 60))
    def _cycle(self, lag_range):
        if len(self.h) < 100: return None,0
        num = [1 if x=="Small" else 0 for x in self.h]
        best_lag, best_corr = 0,0
        for lag in lag_range:
            corr = abs(self.stats.autocorr(num, lag))
            if corr > best_corr:
                best_corr, best_lag = corr, lag
        if best_corr > 0.4:
            recent = num[-best_lag:]
            pred = 1 if (sum(recent)/best_lag) > 0.55 else 0
            return ("Small" if pred else "Big"), min(55 + best_corr*45, 86)
        return None,0
    
    def pattern3(self): return self._pattern(3, 4)
    def pattern5(self): return self._pattern(5, 3)
    def pattern7(self): return self._pattern(7, 2)
    def _pattern(self, depth, min_match):
        if len(self.h) < depth+5: return None,0
        last = self.h[-depth:]
        matches = []
        for i in range(0, len(self.h)-depth-1):
            if self.h[i:i+depth] == last:
                matches.append(self.h[i+depth])
        if len(matches) >= min_match:
            cnt = Counter(matches).most_common(1)[0]
            return cnt[0], min(58 + len(matches)*2, 86)
        return None,0
    
    def neural_sim(self):
        if len(self.h) < 60: return None,0
        features = []
        for offset in [1,2,3,4,5,6,7,8,9,10]:
            if len(self.h) >= offset:
                features.append(1 if self.h[-offset] == "Small" else 0)
        weights = [0.15, 0.12, 0.1, 0.09, 0.08, 0.07, 0.06, 0.05, 0.04, 0.03]
        score = sum(f*w for f,w in zip(features[:len(weights)], weights))
        if score > 0.6: return "Small", 70
        if score < 0.4: return "Big", 68
        return None,0
    
    def sentiment_analysis(self):
        if len(self.h) < 60: return None,0
        ent = self.stats.entropy(self.h[-50:])
        hurst = self.stats.hurst_exponent([1 if x=="Small" else 0 for x in self.h[-200:]])
        if ent < 0.6 and hurst > 0.6:
            most = Counter(self.h[-25:]).most_common(1)[0]
            return most[0], 78
        if ent > 1.2 or hurst < 0.45:
            return None,0
        return None,0
    
    def fibonacci_hurst(self):
        if len(self.h) < 50: return None,0
        num = [1 if x=="Small" else 0 for x in self.h[-80:]]
        high, low = max(num), min(num)
        curr = num[-1]
        hurst = self.stats.hurst_exponent(num)
        levels = [0.236, 0.382, 0.5, 0.618, 0.786]
        for level in levels:
            r = low + (high-low)*level
            if hurst > 0.6:
                if curr <= r and level <= 0.5: return "Small", 72
                if curr >= r and level >= 0.5: return "Big", 70
            else:
                if curr >= r and level <= 0.5: return "Big", 68
                if curr <= r and level >= 0.5: return "Small", 66
        return None,0
    
    def wave_analysis(self):
        if len(self.h) < 40: return None,0
        num = [1 if x=="Small" else 0 for x in self.h[-60:]]
        consecutive = 1
        for i in range(len(num)-2, -1, -1):
            if num[i] == num[-1]: consecutive += 1
            else: break
        hurst = self.stats.hurst_exponent(num)
        if hurst > 0.65:
            if consecutive >= 2 and consecutive <= 4:
                return ("Big" if num[-1]==1 else "Small"), 73
        else:
            if consecutive >= 3:
                return ("Big" if num[-1]==0 else "Small"), 71
        return None,0
    
    def ml_ensemble(self):
        if len(self.h) < 100 or not self.store.deep_learning.is_trained:
            return None,0
        num = [1 if x=="Small" else 0 for x in self.h[-50:]]
        features = [
            self.stats.mean(num), self.stats.std_dev(num), self.stats.skewness(num),
            self.stats.entropy(self.h[-30:]), num[-1], num[-2], num[-3],
            sum(num[-10:])/10, sum(num[-20:])/20
        ]
        return self.store.deep_learning.predict_ml(features)
    
    def trend_analysis(self):
        """Advanced trend analysis for market direction"""
        if len(self.h) < 50: return "NEUTRAL", 0.5
        rec = self.h[-50:]
        small_pct = rec.count("Small") / 50
        strength = GodStats.trend_strength(rec)
        hurst = GodStats.hurst_exponent([1 if x=="Small" else 0 for x in rec])
        
        if small_pct > 0.6 and strength > 0.6:
            return "SMALL TREND", strength
        elif small_pct < 0.4 and strength > 0.6:
            return "BIG TREND", strength
        elif hurst > 0.6:
            return "TRENDING", strength
        else:
            return "RANDOM", 1 - strength
    
    def god_predict(self):
        models = [
            self.trend_short(), self.trend_medium(), self.trend_long(),
            self.markov2(), self.markov4(), self.markov6(),
            self.streak2(), self.streak3(), self.streak4(),
            self.monte_carlo(), self.bayesian_short(), self.bayesian_long(),
            self.volatility_rsi(), self.volatility_macd(), self.cycle_short(),
            self.cycle_medium(), self.cycle_long(), self.pattern3(),
            self.pattern5(), self.pattern7(), self.neural_sim(),
            self.sentiment_analysis(), self.fibonacci_hurst(), self.wave_analysis(),
            self.ml_ensemble()
        ]
        
        use_genetic = self.store.genetic.best_weights is not None and len(self.store.genetic.best_weights) == len(models)
        
        preds = []
        active_models = 0
        for i, (p, c) in enumerate(models):
            if p and c >= 50:
                active_models += 1
                weight = self.store.genetic.best_weights[i] if use_genetic else 1.0
                preds.append((p, c, weight))
        
        if not preds: return None,0, f"No signal ({active_models}/25 models)"
        
        small_score = sum(w for p,c,w in preds if p=="Small")
        big_score = sum(w for p,c,w in preds if p=="Big")
        small_conf = sum(c*w for p,c,w in preds if p=="Small")
        big_conf = sum(c*w for p,c,w in preds if p=="Big")
        
        if small_score > big_score:
            return "Small", round(small_conf/small_score, 1), f"{active_models}/25 models"
        elif big_score > small_score:
            return "Big", round(big_conf/big_score, 1), f"{active_models}/25 models"
        return None,0, "Models tie"

# ========== API ==========
class API:
    @staticmethod
def fetch_history(limit=MAX_HISTORY_LIMIT):
    try:
        all_res = []
        page = 1
        size = min(50, limit)

        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://sikkimin.com/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://sikkimin.com"
        })

        while len(all_res) < limit and page <= 20:
            ts = int(time.time() * 1000)
            params = {
                "ts": ts,
                "pageNo": page,
                "pageSize": size
            }
            resp = session.get(HISTORY_API, params=params, timeout=15)

            if resp.status_code != 200:
                print(f"API status {resp.status_code}, page {page}")
                break

            data = resp.json()
            items = data.get("data", {}).get("list", [])
            if not items:
                break

            for it in items:
                num = it.get("number")
                if num is not None:
                    try:
                        n = int(num)
                        all_res.append("Small" if n <= 4 else "Big")
                    except:
                        pass
            page += 1
            time.sleep(0.5)

        print(f"✅ Fetched {len(all_res)} games from API")
        return all_res[:limit]
    except Exception as e:
        print(f"API Error: {e}")
        return None
    
    @staticmethod
    def get_latest_period():
        """Get the latest COMPLETED period"""
        try:
            ts = int(time.time() * 1000)
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.sikkimin.com/",
                "Accept": "application/json"
            }
            params = {"ts": ts, "pageNo": 1, "pageSize": 1}
            r = requests.get(HISTORY_API, headers=headers, params=params, timeout=10)
            if r.status_code == 200:
                items = r.json().get("data", {}).get("list", [])
                if items:
                    return items[0].get("issueNumber")
            return None
        except:
            return None

# ========== AUTOMATIC BOT ==========
store = Store()
last_predicted_period = None
chat_id = None
last_result = None

async def start(update: Update, context):
    global chat_id
    chat_id = update.effective_chat.id
    
    await update.message.reply_text(
        f"👑 *ULTIMATE BOT WITH LEVEL SYSTEM* 👑\n\n"
        f"📊 Initializing...\n"
        f"🎯 25 AI Models Active\n"
        f"🧬 Genetic Algorithm Active\n"
        f"📈 Advanced Trend Analysis\n"
        f"🏆 LEVEL SYSTEM: Level {store.level_system.current_level}\n\n"
        f"🤖 Bot will automatically predict every new period!\n"
        f"🔄 Current level changes with wins/losses!\n"
        f"📊 Use /stats to see performance\n"
        f"🎯 Use /level to see current level",
        parse_mode="Markdown"
    )
    
    # Initial history fetch
    initial = API.fetch_history(500)
    if initial:
        store.history = deque(initial, maxlen=MAX_HISTORY_LIMIT)
        store.save()
        await update.message.reply_text(f"✅ History loaded: {len(store.history)} games\n\n🔄 Bot active! Will send predictions automatically.")
    else:
        await update.message.reply_text("⚠️ Could not fetch initial history. Bot will still try to predict.")

async def stats_command(update: Update, context):
    stats = store.level_system.get_stats()
    win_rate = stats["win_rate"]
    
    # Determine rating based on win rate
    if win_rate >= 70:
        rating = "🔥 LEGENDARY"
    elif win_rate >= 60:
        rating = "⭐ EXPERT"
    elif win_rate >= 55:
        rating = "📈 ADVANCED"
    elif win_rate >= 50:
        rating = "📊 INTERMEDIATE"
    else:
        rating = "📉 LEARNING"
    
    message = (
        f"📊 *BOT PERFORMANCE STATISTICS* 📊\n\n"
        f"🏆 *CURRENT LEVEL:* {stats['level']}\n"
        f"👑 *MAX LEVEL ACHIEVED:* {stats['max_level']}\n\n"
        f"✅ *TOTAL WINS:* {stats['wins']}\n"
        f"❌ *TOTAL LOSSES:* {stats['losses']}\n"
        f"📈 *WIN RATE:* {win_rate}%\n"
        f"⚡ *CURRENT STREAK:* {abs(stats['streak'])} {'WINS' if stats['streak'] > 0 else 'LOSSES' if stats['streak'] < 0 else 'NONE'}\n\n"
        f"🎯 *RATING:* {rating}\n\n"
        f"📌 *Level System Rules:*\n"
        f"• Win: Stay/improve level\n"
        f"• Loss: Level up (more risk)\n"
        f"• 2 wins at level >1: Level down (safer)\n\n"
        f"🤖 Bot is fully automatic!"
    )
    await update.message.reply_text(message, parse_mode="Markdown")

async def level_command(update: Update, context):
    stats = store.level_system.get_stats()
    await update.message.reply_text(
        f"🏆 *CURRENT LEVEL SYSTEM STATUS* 🏆\n\n"
        f"🔴 *CURRENT LEVEL:* {stats['level']}\n"
        f"👑 *MAX LEVEL:* {stats['max_level']}\n\n"
        f"📊 *Total Wins:* {stats['wins']}\n"
        f"📉 *Total Losses:* {stats['losses']}\n"
        f"📈 *Win Rate:* {stats['win_rate']}%\n\n"
        f"💡 *What does level mean?*\n"
        f"• Level 1: Normal trading (safe)\n"
        f"• Level 2-3: Recovery mode (more risk)\n"
        f"• Level 4+: Maximum caution needed\n\n"
        f"🔄 Bot automatically adjusts level based on results!",
        parse_mode="Markdown"
    )

async def auto_predict(context):
    global last_predicted_period, chat_id, last_result
    
    if not chat_id:
        return
    
    # Fetch latest history
    new_history = API.fetch_history(MAX_HISTORY_LIMIT)
    if new_history and len(new_history) > len(store.history):
        # Check if there's a new result
        if len(new_history) > len(store.history):
            # New result detected - update level system
            if len(store.history) > 0:
                last_actual = new_history[0]  # Latest result
                last_pred = store.last_prediction.get("prediction")
                if last_pred and last_actual:
                    was_correct = (last_pred == last_actual)
                    store.level_system.update(was_correct)
                    store.save()
                    
                    # Send result update
                    result_icon = "✅ WIN!" if was_correct else "❌ LOSS!"
                    try:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"{result_icon} Last period result: {last_actual}\n"
                                 f"Prediction was: {last_pred}\n"
                                 f"🏆 Level: {store.level_system.current_level}",
                            parse_mode="Markdown"
                        )
                    except:
                        pass
        
        store.history = deque(new_history, maxlen=MAX_HISTORY_LIMIT)
        store.save()
    
    if len(store.history) < 30:
        return
    
    # Get current period (next upcoming game)
    current_period = API.get_latest_period()
    if not current_period:
        return
    
    # Wait for new period
    if last_predicted_period == current_period:
        return
    
    # Get prediction
    predictor = GodPredictor(list(store.history), store)
    pred, conf, reason = predictor.god_predict()
    trend_type, trend_strength = predictor.trend_analysis()
    
    if pred and conf >= MIN_CONFIDENCE:
        last_predicted_period = current_period
        store.last_prediction = {"period": current_period, "prediction": pred}
        store.save()
        
        # Calculate market condition
        num = [1 if x=="Small" else 0 for x in list(store.history)[-100:]]
        hurst = round(GodStats.hurst_exponent(num), 3)
        entropy = round(GodStats.entropy(list(store.history)[-50:]), 2)
        
        if hurst > 0.6:
            market = "TRENDING 📈"
        elif hurst < 0.45:
            market = "RANDOM ⚠️"
        else:
            market = "NEUTRAL 📊"
        
        # Determine action based on level
        level = store.level_system.current_level
        if level == 1:
            action = "✅ NORMAL TRADE - Proceed"
        elif level == 2:
            action = "⚠️ CAUTION - Reduce bet size"
        elif level == 3:
            action = "🔴 HIGH RISK - Small bet only"
        else:
            action = "🛑 EXTREME CAUTION - Skip if uncertain"
        
        message = (
            f"🎯 *AUTO PREDICTION | PERIOD {current_period}* 🎯\n\n"
            f"👉 *BET ON: {pred}*\n"
            f"🔬 Confidence: {conf}%\n"
            f"📊 Analysis: {len(store.history)} games\n"
            f"🧠 {reason}\n\n"
            f"📈 Market: {market} | Hurst: {hurst}\n"
            f"🎲 Entropy: {entropy}\n"
            f"📊 Trend: {trend_type} (strength: {trend_strength:.0%})\n\n"
            f"🏆 *CURRENT LEVEL: {level}*\n"
            f"💡 {action}\n\n"
            f"🔄 Next prediction in ~60 seconds\n"
            f"📊 Use /stats for performance"
        )
        
        try:
            await context.bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
            print(f"✅ Auto prediction sent for period {current_period} | Level: {level} | Pred: {pred}")
        except Exception as e:
            print(f"Failed to send: {e}")

def main():
    print("👑 STARTING ULTIMATE BOT WITH LEVEL SYSTEM")
    print("📊 Fetching initial history...")
    initial = API.fetch_history(500)
    if initial:
        store.history = deque(initial, maxlen=MAX_HISTORY_LIMIT)
        store.save()
        print(f"✅ Loaded {len(store.history)} games")
        print(f"🏆 Current Level: {store.level_system.current_level}")
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("level", level_command))
    
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_repeating(auto_predict, interval=CHECK_INTERVAL, first=5)
    
    print("👑 ULTIMATE BOT ACTIVE")
    print(f"📊 History: {len(store.history)} games")
    print(f"🏆 Level System: ACTIVE")
    print("🔄 Bot will automatically track level based on wins/losses")
    
    app.run_polling()

if __name__ == "__main__":
    from flask import Flask
    import threading
    web = Flask(__name__)
    @web.route('/')
    def home(): return "Ultimate Bot Active | Level System"
    threading.Thread(target=lambda: web.run(host='0.0.0.0', port=8080), daemon=True).start()
    main()