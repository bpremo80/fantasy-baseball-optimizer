import streamlit as st
import statsapi
import pybaseball as pb
import pandas as pd
import pulp
import json
from datetime import date, datetime
import io
import os
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Tuple, Any

default_year = date.today().year - 1

# ────────────────────────────────────────────────
# Constants & Configuration
# ────────────────────────────────────────────────
PLAYER_NAMES_CACHE_TTL = 86400  # 1 day

HITTER_SLOTS = {'C': 1, '1B': 1, '2B': 1, '3B': 1, 'SS': 1, 'OF': 3, 'UTIL': 2}
PITCHER_SLOTS = {'SP': 2, 'RP': 2, 'P': 4}
BN_SLOTS = 5
IL_SLOTS = 4

HITTER_ELIGIBLE = {
    'C': ['C'], '1B': ['1B'], '2B': ['2B'], '3B': ['3B'], 'SS': ['SS'], 'OF': ['OF'],
    'UTIL': ['C', '1B', '2B', '3B', 'SS', 'OF', 'UTIL']
}

PITCHER_ELIGIBLE = {'SP': ['SP'], 'RP': ['RP'], 'P': ['SP', 'RP', 'P']}

BATTER_STAT_MAP = {
    'R': 'runs', '1B': 'singles', '2B': 'doubles', '3B': 'triples', 'HR': 'homeRuns',
    'RBI': 'rbi', 'SB': 'stolenBases', 'CS': 'caughtStealing', 'BB': 'baseOnBalls',
    'IBB': 'intentionalWalks', 'HBP': 'hitByPitch', 'SO': 'strikeOuts', 'GDP': 'groundIntoDoublePlay'
}

PITCHER_STAT_MAP = {
    'W': 'wins', 'L': 'losses', 'CG': 'completeGames', 'SHO': 'shutouts', 'SV': 'saves',
    'IP': 'inningsPitched', 'H': 'hits', 'ER': 'earnedRuns', 'BB': 'baseOnBalls',
    'IBB': 'intentionalWalks', 'HBP': 'hitByPitch', 'SO': 'strikeouts', 'WP': 'wildPitches',
    'HLD': 'holds', 'BS': 'blownSaves'
}

# ────────────────────────────────────────────────
# Utility Functions
# ────────────────────────────────────────────────

@st.cache_data(ttl=PLAYER_NAMES_CACHE_TTL)
def load_player_names(year: int) -> List[str]:
    """Load list of player names for auto-complete."""
    try:
        bat = pb.batting_stats(year, qual=0)['Name'].tolist()
        pit = pb.pitching_stats(year, qual=0)['Name'].tolist()
        return sorted(set(bat + pit))
    except Exception:
        return [
            "Aaron Judge", "Shohei Ohtani", "Paul Skenes", "Mookie Betts", "Freddie Freeman",
            "Riley Greene", "Tarik Skubal", "Colt Keith", "Spencer Torkelson", "Kyle Finnegan",
            "Dillon Dingler", "Juan Soto", "Kerry Carpenter", "Bobby Witt Jr.", "Julio Rodriguez",
            "Kenley Jansen", "Will Vest", "Jac Caglianone"
        ]

def calculate_points(stats_dict: Dict, mapping: Dict, scoring: Dict) -> float:
    """Calculate fantasy points from stats dictionary using mapping and scoring."""
    points = 0.0
    for stat, coeff in scoring.items():
        api_key = mapping.get(stat, stat)
        value = stats_dict.get(api_key, 0)
        try:
            points += float(value) * coeff
        except (ValueError, TypeError):
            pass  # skip invalid values
    return points

def fetch_historical_points(player: Dict, year: int) -> float:
    """Fetch historical stats and calculate points."""
    group = 'hitting' if player['type'] == 'batter' else 'pitching'
    stats = statsapi.player_stat_data(player['id'], group=group, type='season', sportId=1, season=year)
    if 'stats' in stats and stats['stats'] and isinstance(stats['stats'][0], dict) and 'stats' in stats['stats'][0]:
        stats_dict = stats['stats'][0]['stats']
        mapping = BATTER_STAT_MAP if player['type'] == 'batter' else PITCHER_STAT_MAP
        scoring = batter_scoring if player['type'] == 'batter' else pitcher_scoring
        return calculate_points(stats_dict, mapping, scoring)
    return 0.0

def fetch_projections(player_name: str, player_type: str) -> float:
    """Scrape FanGraphs Steamer projections."""
    projection_points = 0.0
    try:
        fg_pos = 'all' if player_type == 'batter' else 'pitching'
        search_name = player_name.lower().replace(' ', '-').replace('.', '')
        url = f"https://www.fangraphs.com/players/{search_name}/stats?position={fg_pos.upper()}"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            table = soup.find('table', class_='rgMasterTable')
            if table:
                for row in table.find_all('tr'):
                    if 'Steamer' in row.get_text():
                        cols = row.find_all('td')
                        if len(cols) > 10:
                            if player_type == 'batter':
                                r = float(cols[4].text.strip() or 0)
                                hr = float(cols[7].text.strip() or 0)
                                rbi = float(cols[8].text.strip() or 0)
                                sb = float(cols[9].text.strip() or 0)
                                projection_points = (
                                    r * batter_scoring.get('R', 0) +
                                    hr * batter_scoring.get('HR', 0) +
                                    rbi * batter_scoring.get('RBI', 0) +
                                    sb * batter_scoring.get('SB', 0)
                                )
                            else:
                                w = float(cols[6].text.strip() or 0)
                                sv = float(cols[8].text.strip() or 0)
                                ip = float(cols[4].text.strip() or 0)
                                so = float(cols[10].text.strip() or 0)
                                projection_points = (
                                    w * pitcher_scoring.get('W', 0) +
                                    sv * pitcher_scoring.get('SV', 0) +
                                    ip * pitcher_scoring.get('IP', 0) +
                                    so * pitcher_scoring.get('SO', 0)
                                )
    except Exception as e:
        st.warning(f"Projections failed for {player_name}: {str(e)}")
    return projection_points

# ────────────────────────────────────────────────
# Main App
# ────────────────────────────────────────────────
st.title("Fantasy Baseball Lineup Optimizer")

# Scoring
st.header("Scoring Systems")
with st.expander("Edit Scoring if Needed", expanded=False):
    batter_scoring_str = st.text_area("Batter Scoring (JSON)", value='{"R": 1, "1B": 1, "2B": 2, "3B": 3, "HR": 4, "RBI": 1, "SB": 2, "CS": -1, "BB": 1, "IBB": 1, "HBP": 1, "SO": -1, "GDP": -1}')
    pitcher_scoring_str = st.text_area("Pitcher Scoring (JSON)", value='{"W": 10, "L": -5, "CG": 10, "SHO": 5, "SV": 10, "IP": 3, "H": -1, "ER": -1, "BB": -1, "IBB": -1, "HBP": -1.3, "SO": 1, "WP": -1, "HLD": 7, "BS": -5}')

try:
    batter_scoring = json.loads(batter_scoring_str)
    pitcher_scoring = json.loads(pitcher_scoring_str)
except json.JSONDecodeError:
    st.error("Invalid scoring JSON. Fix and retry.")
    st.stop()

# Roster
st.header("Step 1: Build or Upload Roster")

if 'roster' not in st.session_state:
    st.session_state.roster = []

uploaded_file = st.file_uploader("Upload Roster CSV", type="csv")
if uploaded_file:
    df = pd.read_csv(uploaded_file)
    st.session_state.roster = []
    for _, row in df.iterrows():
        positions = row.get('positions', '').split(',')
        st.session_state.roster.append({
            "name": row['name'],
            "type": row['type'],
            "positions": [p.strip() for p in positions if p.strip()]
        })
    st.success("Roster uploaded!")

with st.form("Add Player", clear_on_submit=True):
    cols = st.columns(3)
    with cols[0]:
        name = st.selectbox("Player Name (type to search)", options=[""] + player_names, index=0, placeholder="Start typing last name...")
    with cols[1]:
        typ = st.selectbox("Type", ['batter', 'pitcher'])
    with cols[2]:
        positions = st.multiselect("Eligible Positions", options=['C', '1B', '2B', '3B', 'SS', 'OF', 'UTIL', 'SP', 'RP', 'P', 'BN', 'IL'])
    add = st.form_submit_button("Add")

if add and name:
    st.session_state.roster.append({"name": name, "type": typ, "positions": positions})
    st.success(f"Added {name}")
    st.rerun()

st.subheader("Current Roster")
for i, p in enumerate(st.session_state.roster):
    cols = st.columns(4)
    with cols[0]: st.write(p['name'])
    with cols[1]: st.write(p['type'])
    with cols[2]: st.write(', '.join(p['positions']) or 'None')
    with cols[3]:
        if st.button("Remove", key=f"rem_{i}"):
            del st.session_state.roster[i]
            st.rerun()

if st.session_state.roster:
    df = pd.DataFrame(st.session_state.roster)
    df['positions'] = df['positions'].apply(lambda x: ','.join(x))
    csv = df.to_csv(index=False).encode('utf-8')
    st.download_button("Download Roster CSV", csv, "roster.csv", "text/csv")

# Optimizer
st.header("Step 2: Select Year & Run Optimizer")
year = st.number_input("Season Year", 1871, date.today().year, value=default_year)

if st.button("Fetch Stats & Optimize"):
    if not st.session_state.roster:
        st.error("Build roster first.")
    else:
        with st.spinner("Fetching data..."):
            roster = st.session_state.roster.copy()
            unmatched = []
            for player in roster:
                if 'IL' in player['positions']:
                    player['points'] = 0
                    continue

                search = statsapi.lookup_player(player['name'])
                if not search:
                    unmatched.append(player['name'])
                    player['points'] = 0
                    continue

                player['id'] = search[0]['id']

                # Historical
                historical_points = fetch_historical_points(player, year)

                # Projections
                projection_points = fetch_projections(player['name'], player['type'])

                player['points'] = historical_points + projection_points

            if unmatched:
                st.warning(f"No data for: {', '.join(unmatched)}")

            # Optimization
            hitters = [p for p in roster if p['type'] == 'batter' and 'IL' not in p['positions']]
            pitchers = [p for p in roster if p['type'] == 'pitcher' and 'IL' not in p['positions']]

            hitter_lineup, hitter_pts, hitter_leftover = optimize(hitters, HITTER_SLOTS, hitter_eligible)
            pitcher_lineup, pitcher_pts, pitcher_leftover = optimize(pitchers, PITCHER_SLOTS, pitcher_eligible)

            leftover = hitter_leftover + pitcher_leftover
            leftover.sort(key=lambda p: p['points'], reverse=True)
            bn = [f"{p['name']} ({p['points']:.2f})" for p in leftover[:BN_SLOTS]]
            unused = leftover[BN_SLOTS:]

            il_players = [p for p in roster if 'IL' in p['positions']]
            il = [f"{p['name']} (0 pts)" for p in il_players[:IL_SLOTS]]
            extra_il = il_players[IL_SLOTS:]

            st.header("Optimized Lineup")
            st.subheader("Hitters")
            for slot, assigned in hitter_lineup.items():
                st.write(f"{slot}: {', '.join(assigned) or 'None'}")
            st.write(f"Hitter Points: {hitter_pts:.2f}")

            st.subheader("Pitchers")
            for slot, assigned in pitcher_lineup.items():
                st.write(f"{slot}: {', '.join(assigned) or 'None'}")
            st.write(f"Pitcher Points: {pitcher_pts:.2f}")

            st.write(f"Grand Total: {(hitter_pts + pitcher_pts):.2f}")

            st.subheader("Bench (5 Slots)")
            st.write(', '.join(bn) or 'None')

            st.subheader("IL (4 Slots)")
            st.write(', '.join(il) or 'None')
            if extra_il:
                st.info(f"Extra IL: {', '.join([p['name'] for p in extra_il])}")

            if unused:
                st.info(f"Unused: {', '.join([f'{p['name']} ({p['points']:.2f})' for p in unused])}")

# Reset
if st.button("Reset Roster"):
    st.session_state.roster = []
    st.rerun()
