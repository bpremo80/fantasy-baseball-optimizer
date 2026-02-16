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

default_year = date.today().year - 1

# Pre-load player names for auto-complete
@st.cache_data(ttl=86400)
def load_player_names(year):
    try:
        bat = pb.batting_stats(year, qual=0)['Name'].tolist()
        pit = pb.pitching_stats(year, qual=0)['Name'].tolist()
        return sorted(set(bat + pit))
    except:
        return ["Aaron Judge", "Shohei Ohtani", "Paul Skenes", "Mookie Betts", "Freddie Freeman", "Riley Greene", "Tarik Skubal", "Colt Keith", "Spencer Torkelson", "Kyle Finnegan"]

player_names = load_player_names(2025)

# Mobile-friendly config
st.set_page_config(layout="wide", page_title="Fantasy Baseball Optimizer")
st.markdown("""
<style>
    .stApp { max-width: 100%; }
    .block-container { padding-top: 1rem; padding-bottom: 1rem; }
    .stTextInput > div > div > input { font-size: 14px; }
    .stSelectbox > div > div > select { font-size: 14px; }
    .stMultiselect > div > div > ul { font-size: 14px; }
    .stButton > button { font-size: 14px; }
</style>
""", unsafe_allow_html=True)

st.title("Fantasy Baseball Lineup Optimizer")

# Scoring Systems
st.header("Scoring Systems")
with st.expander("Edit Scoring if Needed", expanded=False):
    batter_scoring_str = st.text_area(
        "Batter Scoring (JSON)",
        value='{"R": 1, "1B": 1, "2B": 2, "3B": 3, "HR": 4, "RBI": 1, "SB": 2, "CS": -1, "BB": 1, "IBB": 1, "HBP": 1, "SO": -1, "GDP": -1}'
    )
    pitcher_scoring_str = st.text_area(
        "Pitcher Scoring (JSON)",
        value='{"W": 10, "L": -5, "CG": 10, "SHO": 5, "SV": 10, "IP": 3, "H": -1, "ER": -1, "BB": -1, "IBB": -1, "HBP": -1.3, "SO": 1, "WP": -1, "HLD": 7, "BS": -5}'
    )

try:
    batter_scoring = json.loads(batter_scoring_str)
    pitcher_scoring = json.loads(pitcher_scoring_str)
except json.JSONDecodeError:
    st.error("Invalid scoring JSON. Fix and retry.")
    st.stop()

# MLB Stats API mappings
batter_map = {
    'R': 'runs', '1B': 'singles', '2B': 'doubles', '3B': 'triples', 'HR': 'homeRuns',
    'RBI': 'rbi', 'SB': 'stolenBases', 'CS': 'caughtStealing', 'BB': 'baseOnBalls',
    'IBB': 'intentionalWalks', 'HBP': 'hitByPitch', 'SO': 'strikeOuts', 'GDP': 'groundIntoDoublePlay'
}

pitcher_map = {
    'W': 'wins', 'L': 'losses', 'CG': 'completeGames', 'SHO': 'shutouts', 'SV': 'saves',
    'IP': 'inningsPitched', 'H': 'hits', 'ER': 'earnedRuns', 'BB': 'baseOnBalls',
    'IBB': 'intentionalWalks', 'HBP': 'hitByPitch', 'SO': 'strikeouts', 'WP': 'wildPitches',
    'HLD': 'holds', 'BS': 'blownSaves'
}

# Roster Management
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
        name = st.selectbox(
            "Player Name (type to search)",
            options=[""] + player_names,
            index=0,
            placeholder="Start typing last name..."
        )
    with cols[1]:
        typ = st.selectbox("Type", ['batter', 'pitcher'])
    with cols[2]:
        positions = st.multiselect(
            "Eligible Positions",
            options=['C', '1B', '2B', '3B', 'SS', 'OF', 'UTIL', 'SP', 'RP', 'P', 'BN', 'IL']
        )
    add = st.form_submit_button("Add")

if add and name:
    st.session_state.roster.append({"name": name, "type": typ, "positions": positions})
    st.success(f"Added {name}")
    st.rerun()

st.subheader("Current Roster")
for i, p in enumerate(st.session_state.roster):
    cols = st.columns(4)
    with cols[0]:
        st.write(p['name'])
    with cols[1]:
        st.write(p['type'])
    with cols[2]:
        st.write(', '.join(p['positions']) or 'None')
    with cols[3]:
        if st.button("Remove", key=f"rem_{i}"):
            del st.session_state.roster[i]
            st.rerun()

# Export Roster
if st.session_state.roster:
    df = pd.DataFrame(st.session_state.roster)
    df['positions'] = df['positions'].apply(lambda x: ','.join(x))
    csv = df.to_csv(index=False).encode('utf-8')
    st.download_button("Download Roster CSV", csv, "roster.csv", "text/csv")

# Optimizer
st.header("Step 2: Select Year & Run Optimizer")
year = st.number_input("Season Year", 1871, date.today().year, value=default_year)

if st.button("Fetch Stats, Projections & Optimize"):
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

                # Get player ID
                search = statsapi.lookup_player(player['name'])
                if not search:
                    unmatched.append(player['name'])
                    player['points'] = 0
                    continue

                player_id = search[0]['id']

                # Historical Stats
                group = 'hitting' if player['type'] == 'batter' else 'pitching'
                stats = statsapi.player_stat_data(player_id, group=group, type='season', sportId=1, season=year)
                historical_points = 0.0
                if 'stats' in stats and stats['stats'] and isinstance(stats['stats'][0], dict) and 'stats' in stats['stats'][0]:
                    stats_dict = stats['stats'][0]['stats']
                    mapping = batter_map if player['type'] == 'batter' else pitcher_map
                    scoring = batter_scoring if player['type'] == 'batter' else pitcher_scoring
                    
                    historical_points = sum(
                        float(stats_dict.get(mapping.get(stat, stat), 0)) * coeff 
                        for stat, coeff in scoring.items()
                    )

                # Projections: scrape FanGraphs Steamer
                projection_points = 0.0
                try:
                    fg_pos = 'all' if player['type'] == 'batter' else 'pitching'
                    search_name = player['name'].lower().replace(' ', '-').replace('.', '')
                    fg_url = f"https://www.fangraphs.com/players/{search_name}/stats?position={fg_pos.upper()}"
                    headers = {"User-Agent": "Mozilla/5.0"}
                    response = requests.get(fg_url, headers=headers, timeout=10)
                    if response.status_code == 200:
                        soup = BeautifulSoup(response.text, 'html.parser')
                        table = soup.find('table', class_='rgMasterTable')
                        if table:
                            rows = table.find_all('tr')
                            for row in rows:
                                if 'Steamer' in row.text:
                                    cols = row.find_all('td')
                                    if len(cols) > 10:
                                        if player['type'] == 'batter':
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
                    if projection_points > 0:
                        st.write(f"**Projections SUCCESS** for {player['name']}: {projection_points:.2f} points")
                    else:
                        st.write(f"**Projections FAILED** (0 points) for {player['name']}")
                except Exception as e:
                    st.warning(f"Projections failed for {player['name']}: {str(e)}")
                    projection_points = 0.0

                player['points'] = historical_points + projection_points

            if unmatched:
                st.warning(f"No data for: {', '.join(unmatched)}")

            # Strict optimization (no duplicate use)
            hitters = [p for p in roster if p['type'] == 'batter' and 'IL' not in p['positions']]
            pitchers = [p for p in roster if p['type'] == 'pitcher' and 'IL' not in p['positions']]

            hitter_slots = {'C': 1, '1B': 1, '2B': 1, '3B': 1, 'SS': 1, 'OF': 3, 'UTIL': 2}
            pitcher_slots = {'SP': 2, 'RP': 2, 'P': 4}
            bn_slots = 5
            il_slots = 4

            hitter_eligible = {
                'C': ['C'], '1B': ['1B'], '2B': ['2B'], '3B': ['3B'], 'SS': ['SS'], 'OF': ['OF'],
                'UTIL': ['C', '1B', '2B', '3B', 'SS', 'OF', 'UTIL']
            }
            pitcher_eligible = {'SP': ['SP'], 'RP': ['RP'], 'P': ['SP', 'RP', 'P']}

            def optimize(players, slots, eligible):
                if not players:
                    return {}, 0.0, players
                prob = pulp.LpProblem("Optimizer", pulp.LpMaximize)
                x = {}
                for i, p in enumerate(players):
                    for s in slots:
                        if set(p['positions']) & set(eligible[s]):
                            x[(i, s)] = pulp.LpVariable(f"x_{i}_{s}", cat='Binary')
                prob += pulp.lpSum(x[(i, s)] * p['points'] for i, s in x)
                for s in slots:
                    prob += pulp.lpSum(x.get((i, s), 0) for i in range(len(players))) == slots[s]
                # Strict: each player used in at most ONE slot total
                for i in range(len(players)):
                    prob += pulp.lpSum(x.get((i, s), 0) for s in slots) <= 1
                prob.solve(pulp.PULP_CBC_CMD(msg=False))
                lineup = {s: [] for s in slots}
                used = set()
                for var in x:
                    if x[var].value() == 1:
                        i, s = var
                        lineup[s].append(f"{players[i]['name']} ({players[i]['points']:.2f})")
                        used.add(i)
                total = pulp.value(prob.objective) or 0.0
                leftover = [players[i] for i in range(len(players)) if i not in used]
                return lineup, total, leftover

            hitter_lineup, hitter_pts, hitter_leftover = optimize(hitters, hitter_slots, hitter_eligible)
            pitcher_lineup, pitcher_pts, pitcher_leftover = optimize(pitchers, pitcher_slots, pitcher_eligible)

            leftover = hitter_leftover + pitcher_leftover
            leftover.sort(key=lambda p: p['points'], reverse=True)
            bn = [f"{p['name']} ({p['points']:.2f})" for p in leftover[:bn_slots]]
            unused = leftover[bn_slots:]

            il_players = [p for p in roster if 'IL' in p['positions']]
            il = [f"{p['name']} (0 pts)" for p in il_players[:il_slots]]
            extra_il = [f"{p['name']} (0 pts)" for p in il_players[il_slots:]] if len(il_players) > il_slots else []

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
                st.info(f"Extra IL: {', '.join(extra_il)}")

            if unused:
                st.info(f"Unused: {', '.join(f'{p['name']} ({p['points']:.2f})' for p in unused)}")

# Reset Roster
if st.button("Reset Roster"):
    st.session_state.roster = []
    st.rerun()
