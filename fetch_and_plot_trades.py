import requests
import pandas as pd
import matplotlib.pyplot as plt
import datetime

# 1. Configuration
EVENT_QUERY = "Bologna"
TEAM_2_QUERY = "Inter"
MARKET_QUERY = "3.5"

# If the search fails, paste the slug from the Polymarket URL here:
# Example: "bologna-vs-inter-milano-may-23"
EVENT_SLUG = ""

CSV_FILENAME = "bologna_inter_ou35_history.csv"
PLOT_FILENAME = "bologna_inter_ou35_plot.png"

def fetch_market_info():
    """Finds the event and market on Polymarket."""
    url = "https://gamma-api.polymarket.com/events"
    
    if EVENT_SLUG:
        print(f"Fetching event exactly by slug: {EVENT_SLUG}")
        r = requests.get(url, params={'slug': EVENT_SLUG})
        events = r.json()
    else:
        print("Searching Gamma API for the event...")
        events = []
        # Fetch up to 2000 events to find it
        for offset in [0, 500, 1000, 1500]:
            r = requests.get(url, params={'limit': 500, 'offset': offset})
            data = r.json()
            if not data: break
            events.extend(data)
            
    if not events:
        return None
        
    for e in events:
        title = e.get('title', '')
        if EVENT_SLUG or (EVENT_QUERY in title and (TEAM_2_QUERY in title or "Milano" in title)):
            print(f"Found Event: {title}")
            for m in e.get('markets', []):
                question = m.get('question', '')
                if MARKET_QUERY in question:
                    print(f"Found Market: {question}")
                    return m
    return None

def fetch_price_history(condition_id):
    """Fetches the historical price data from the CLOB."""
    print(f"Fetching price history for condition ID: {condition_id}")
    url = "https://clob.polymarket.com/prices-history"
    
    # interval=all gets the maximum available history for the market
    params = {
        'interval': 'all',
        'market': condition_id
    }
    
    r = requests.get(url, params=params)
    if r.status_code != 200:
        print(f"Error fetching history: {r.status_code} - {r.text}")
        return []
        
    data = r.json()
    return data.get('history', [])

def process_and_plot(history_data, market_info):
    if not history_data:
        print("No historical data found for this market.")
        return
        
    print(f"Processing {len(history_data)} data points...")
    
    # The history data is usually a list of dicts: {"t": timestamp_unix, "p": price}
    # For binary markets, the price returned is generally for the "Yes" or "Over" token.
    # The "Under" token price is 1 - price.
    
    df = pd.DataFrame(history_data)
    
    # Convert timestamp to datetime
    df['datetime'] = pd.to_datetime(df['t'], unit='s')
    
    # Calculate prices
    df['price_over'] = df['p']
    df['price_under'] = 1.0 - df['p']
    
    # Save to CSV
    df.to_csv(CSV_FILENAME, index=False)
    print(f"Saved data to {CSV_FILENAME}")
    
    # Plot
    plt.figure(figsize=(12, 6))
    plt.plot(df['datetime'], df['price_over'], label='Over 3.5', color='blue', linewidth=2)
    plt.plot(df['datetime'], df['price_under'], label='Under 3.5', color='red', linewidth=2)
    
    plt.title(f"Polymarket Price History\n{market_info.get('question')}")
    plt.xlabel('Date/Time (UTC)')
    plt.ylabel('Probability / Price ($)')
    plt.ylim(0, 1.0)
    plt.grid(True, alpha=0.3)
    plt.legend()
    
    # Rotate x-axis labels for better readability
    plt.xticks(rotation=45)
    plt.tight_layout()
    
    # Save Plot
    plt.savefig(PLOT_FILENAME)
    print(f"Saved plot to {PLOT_FILENAME}")
    plt.close()

def main():
    market_info = fetch_market_info()
    if not market_info:
        print("Could not find the specified market. The event might have expired or the name might be slightly different.")
        return
        
    condition_id = market_info.get('conditionId')
    if not condition_id:
        print("Market missing conditionId.")
        return
        
    history = fetch_price_history(condition_id)
    process_and_plot(history, market_info)

if __name__ == "__main__":
    main()
