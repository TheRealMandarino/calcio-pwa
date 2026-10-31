import sqlite3
import pandas as pd
import numpy as np
from backend.stats import compute_team_stats
from backend.model import calculate_match_probabilities

def run_backtest_1x2(db_path="data/database.db"):
    conn = sqlite3.connect(db_path)
    
    # Selezioniamo solo le partite della stagione scorsa con risultato noto
    query = """
        SELECT date, league, home_team, away_team, home_goals, away_goals 
        FROM matches 
        WHERE home_goals IS NOT NULL AND away_goals IS NOT NULL 
          AND date >= '2025-07-01' AND date <= '2026-06-30'
        ORDER BY date ASC
    """
    df_matches = pd.read_sql(query, conn)
    conn.close()
    
    if df_matches.empty:
        print("Nessuna partita trovata nel periodo specificato per il backtesting.")
        return

    total_matches = len(df_matches)
    correct_predictions = 0
    
    stats_1_correct = 0
    stats_1_total = 0
    stats_x_correct = 0
    stats_x_total = 0
    stats_2_correct = 0
    stats_2_total = 0
    
    profit_flat = 0.0  # Profitto con stake fisso di 1 unità sul segno con probabilità maggiore

    print(f"Avvio backtesting sul mercato 1X2 per {total_matches} partite (Stagione 2025/2026)...")
    
    stats_cache = compute_team_stats(db_path)

    for _, row in df_matches.iterrows():
        h_team = row['home_team']
        a_team = row['away_team']
        h_goals = int(row['home_goals'])
        a_goals = int(row['away_goals'])
        
        # Determiniamo il segno reale
        if h_goals > a_goals:
            real_sign = "1"
        elif h_goals == a_goals:
            real_sign = "X"
        else:
            real_sign = "2"
            
        league = row['league']
        
        try:
            calc = calculate_match_probabilities(h_team, a_team, league=league, precomputed_stats=stats_cache)
            
            p1 = max(calc["prob_1"], 0.03)
            pX = max(calc["prob_X"], 0.03)
            p2 = max(calc["prob_2"], 0.03)
            total_p = p1 + pX + p2
            p1, pX, p2 = p1 / total_p, pX / total_p, p2 / total_p
            
            # Quota implicita stimata dal modello
            odd_1 = min(round(1.0 / p1, 2), 20.0)
            odd_X = min(round(1.0 / pX, 2), 20.0)
            odd_2 = min(round(1.0 / p2, 2), 20.0)
            
            # Scegliamo il segno con probabilità più alta predetto dal modello
            probs = {"1": (p1, odd_1), "X": (pX, odd_X), "2": (p2, odd_2)}
            predicted_sign = max(probs, key=lambda k: probs[k][0])
            pred_odd = probs[predicted_sign][1]
            
            # Statistiche per singolo segno
            if predicted_sign == "1":
                stats_1_total += 1
            elif predicted_sign == "X":
                stats_x_total += 1
            else:
                stats_2_total += 1
                
            if predicted_sign == real_sign:
                correct_predictions += 1
                profit_flat += (pred_odd - 1.0)
                if predicted_sign == "1": stats_1_correct += 1
                elif predicted_sign == "X": stats_x_correct += 1
                else: stats_2_correct += 1
            else:
                profit_flat -= 1.0
                
        except Exception as e:
            continue

    accuracy = (correct_predictions / total_matches) * 100 if total_matches > 0 else 0
    roi = (profit_flat / total_matches) * 100 if total_matches > 0 else 0

    print("\n" + "="*55)
    print("REPORT BACKTESTING - MERCATO 1X2 (SEGNO PRINCIPALE)")
    print("="*55)
    print(f"Partite analizzate (Stagione scorsa): {total_matches}")
    print(f"Pronostici 1X2 azzeccati           : {correct_predictions}")
    print(f"Accuratezza complessiva            : {accuracy:.2f}%")
    print(f"Profitto Totale (Stake fisso 1U)   : {profit_flat:+.2f} unità")
    print(f"ROI Simulato (Flat Stake)          : {roi:+.2f}%")
    print("-" * 55)
    print("Dettaglio per Segno:")
    acc_1 = (stats_1_correct / stats_1_total * 100) if stats_1_total > 0 else 0
    acc_x = (stats_x_correct / stats_x_total * 100) if stats_x_total > 0 else 0
    acc_2 = (stats_2_correct / stats_2_total * 100) if stats_2_total > 0 else 0
    print(f" - Segno 1 (Casa)    : {stats_1_correct}/{stats_1_total} ({acc_1:.2f}% presi)")
    print(f" - Segno X (Pareggio): {stats_x_correct}/{stats_x_total} ({acc_x:.2f}% presi)")
    print(f" - Segno 2 (Trasf.)  : {stats_2_correct}/{stats_2_total} ({acc_2:.2f}% presi)")
    print("="*55)

if __name__ == "__main__":
    run_backtest_1x2()