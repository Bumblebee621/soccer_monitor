import requests

def get_football_data_with_totals():
    url = "https://gamma-api.polymarket.com/events"
    
    params = {
        "active": "true",
        "closed": "false",
        "limit": 50 
    }

    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        events = response.json()

        print("Analyzing soccer matches and Over/Under data...\n" + "="*50)

        for event in events:
            tags = [tag.get('label', '') for tag in event.get('tags', [])]
            if 'Soccer' not in tags:
                continue

            league_tags = [t for t in tags if t not in ['Soccer', 'Sports']]
            league_name = league_tags[0] if league_tags else "General"
            match_title = event.get('title', 'Unknown Match')

            print(f"League: {league_name}")
            print(f"Match: {match_title}")

            markets = event.get('markets', [])
            
            # List of requested thresholds
            thresholds = ["1.5", "2.5", "3.5", "4.5"]
            
            # Separate winner market from goal markets
            winner_market = None
            total_goals_markets = []

            for m in markets:
                question = m.get('question', '').lower()
                # Identify the winner market (usually the first market or one that doesn't contain 'total')
                if "winner" in question or "match result" in question or "moneyline" in question:
                    winner_market = m
                # Identify goal markets
                if "total goals" in question or "over/under" in question:
                    if any(t in question for t in thresholds):
                        total_goals_markets.append(m)

            # Print winner market
            if winner_market:
                outcomes = winner_market.get('outcomes', [])
                prices = winner_market.get('outcomePrices', [])
                print("   Match Result (1X2/ML):")
                for o, p in zip(outcomes, prices):
                    print(f"      - {o}: {float(p)*100:.1f}%")

            # Print Over/Under markets
            if total_goals_markets:
                print("   Goal Markets (Over/Under):")
                for tm in total_goals_markets:
                    q_text = tm.get('question')
                    t_outcomes = tm.get('outcomes', [])
                    t_prices = tm.get('outcomePrices', [])
                    
                    # Clean up the question name for pretty display
                    display_q = q_text.replace("Will there be ", "").replace("?", "")
                    
                    # PolyMarket usually displays Yes/No for Over/Under
                    # Yes = Over, No = Under
                    price_str = " | ".join([f"{o}: {float(p)*100:.1f}%" for o, p in zip(t_outcomes, t_prices)])
                    print(f"      - {display_q}: {price_str}")
            else:
                print("   No Over/Under markets found for this match.")
                available_questions = [m.get('question') for m in markets]
                if available_questions:
                    print(f"   Note: This event only has Outright/Winner markets: {available_questions[:2]}...")

            print("-" * 50)

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    get_football_data_with_totals()