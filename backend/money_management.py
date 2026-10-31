def calculate_kelly_stake(probability, bookmaker_odd, bankroll=1000.0, kelly_fraction=0.25, max_cap_percentage=0.05):
    """
    Calcola lo stake ottimale usando il Criterio di Kelly Frazionario.
    
    :param probability: Probabilità stimata dal modello (es. 0.45)
    :param bookmaker_odd: Quota del bookmaker (es. 2.40)
    :param bankroll: Bankroll totale del giocatore (es. 1000€)
    :param kelly_fraction: Frazione di Kelly (0.25 = 1/4 Kelly, standard prudente)
    :param max_cap_percentage: Limite massimo di esposizione su singola bet (es. 5%)
    :return: Dizionario con EV, percentuale di Kelly e importo in denaro
    """
    if bookmaker_odd <= 1.0 or probability <= 0.0:
        return {
            "is_value_bet": False,
            "stake_money": 0.0,
            "reason": "Quota o probabilità non valide"
        }

    # 1. Calcolo del Margine di Valore (EV)
    ev = (probability * bookmaker_odd) - 1.0
    ev_percentage = ev * 100.0

    # Se l'EV è <= 0, non c'è valore matematico
    if ev <= 0:
        return {
            "is_value_bet": False,
            "ev_percentage": round(ev_percentage, 2),
            "stake_money": 0.0,
            "reason": "Nessun vantaggio statistico (EV negativo o nullo)"
        }

    # 2. Applicazione della formula di Kelly Pura
    net_odds = bookmaker_odd - 1.0
    kelly_full = (probability * bookmaker_odd - 1.0) / net_odds

    # Se per qualche motivo il kelly puro risulta negativo
    if kelly_full <= 0:
        return {
            "is_value_bet": False,
            "ev_percentage": round(ev_percentage, 2),
            "stake_money": 0.0,
            "reason": "Kelly non favorevole"
        }

    # 3. Kelly Frazionario (es. 25% del Kelly pieno)
    kelly_adjusted = kelly_full * kelly_fraction
    
    # 4. Calcolo dello stake in denaro
    stake_money = bankroll * kelly_adjusted

    # 5. Cap di sicurezza assoluto (es. massimo il 5% del bankroll su una singola bet)
    max_safe_stake = bankroll * max_cap_percentage
    if stake_money > max_safe_stake:
        stake_money = max_safe_stake

    return {
        "is_value_bet": True,
        "ev_percentage": round(ev_percentage, 2),
        "kelly_percentage": round(kelly_adjusted * 100, 2),
        "stake_money": round(stake_money, 2)
    }