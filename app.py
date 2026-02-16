import streamlit as st
import statsapi  # MLB Stats API for current/scheduled data (pip install MLB-StatsAPI)
import pybaseball as pb
import pandas as pd
import pulp
import json
from datetime import date, datetime
import io
import os
default_year = date.today().year - 1
# ────────────────────────────────────────────────
# App Config (Mobile-Friendly)
# ────────────────────────────────────────────────
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

# ────────────────────────────────────────────────
# Scoring Systems (Fixed)
# ────────────────────────────────────────────────
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

# MLB Stats API Mapping (for statsapi.player_stat_data)
batter_map = {
    'R': 'runs', '1B': 'singles', '2B': 'doubles', '3B': 'triples', 'HR': 'homeRuns',
    'RBI': 'rbi', 'SB': 'stolenBases', 'CS': 'caughtStealing', 'BB': 'baseOnBalls', 'IBB': 'intentionalWalks',
    'HBP': 'hitByPitch', 'SO': 'strikeOuts', 'GDP': 'groundIntoDoublePlay'
}

pitcher_map = {
    'W': 'wins', 'L': 'losses', 'CG': 'completeGames', 'SHO': 'shutouts', 'SV': 'saves', 'IP': 'inningsPitched',
    'H': 'hits', 'ER': 'earnedRuns', 'BB': 'baseOnBalls', 'IBB': 'intentionalWalks', 'HBP': 'hitByPitch',
    'SO': 'strikeouts', 'WP': 'wildPitches', 'HLD': 'holds', 'BS': 'blownSaves'
}

# ────────────────────────────────────────────────
# Roster Management (Add, Edit, CSV Upload/Save)
# ────────────────────────────────────────────────
st.header("Step 1: Build or Upload Roster")

# Session State for Roster
if 'roster' not in st.session_state:
    st.session_state.roster = []

# CSV Upload
uploaded_file = st.file_uploader("Upload Roster CSV", type="csv")
if uploaded_file:
    df = pd.read_csv(uploaded_file)
    st.session_state.roster = []
    for _, row in df.iterrows():
        positions = row['positions'].split(',') if 'positions' in row else []
        st.session_state.roster.append({
            "name": row['name'],
            "type": row['type'],
            "positions": positions
        })
    st.success("Roster uploaded!")

# Add Player Form
with st.form("Add Player"):
    cols = st.columns(3)
    with cols[0]:
        name = st.text_input("Player Name")
    with cols[1]:
        typ = st.selectbox("Type", ['batter', 'pitcher'])
    with cols[2]:
        positions = st.multiselect("Positions", ['C', '1B', '2B', '3B', 'SS', 'OF', 'UTIL', 'SP', 'RP', 'P', 'BN', 'IL'])
    add = st.form_submit_button("Add")

if add and name:
    st.session_state.roster.append({"name": name, "type": typ, "positions": positions})
    st.rerun()

# Display & Edit Roster
for i, p in enumerate(st.session_state.roster):
    cols = st.columns(4)
    with cols[0]:
        st.write(p['name'])
    with cols[1]:
        st.write(p['type'])
    with cols[2]:
        st.write(', '.join(p['positions']))
    with cols[3]:
        if st.button("Remove", key=f"rem_{i}"):
            del st.session_state.roster[i]
            st.rerun()

# CSV Export
if st.session_state.roster:
    df = pd.DataFrame(st.session_state.roster)
    df['positions'] = df['positions'].apply(lambda x: ','.join(x))
    csv = df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="Download Roster CSV",
        data=csv,
        file_name="roster.csv",
        mime="text/csv"
    )

# ────────────────────────────────────────────────
# Step 2: Select Year & Fetch Stats/Projections
# ────────────────────────────────────────────────
st.header("Step 2: Select Year & Run Optimizer")
year = st.number_input("Season Year", 1871, date.today().year, default_year)

if st.button("Fetch Stats, Projections & Optimize"):
    if not st.session_state.roster:
        st.error("Build roster first.")
    else:
        with st.spinner("Fetching stats, projections, and matchups..."):
            roster = st.session_state.roster
            unmatched = []
            for player in roster:
                if 'IL' in player['positions']:
                    player['points'] = 0  # IL ineligible
                    continue
                
                # Get player ID
                search = statsapi.lookup_player(player['name'])
                if not search:
                    unmatched.append(player['name'])
                    player['points'] = 0
                    continue
                
                player_id = search[0]['id']
                position = search[0].get('primaryPosition', {}).get('abbreviation', 'Unknown')
                if not player['positions']:
                    player['positions'] = [position] if position != 'Unknown' else ['UTIL' if player['type'] == 'batter' else 'P']
                
                # Historical Stats
                group = 'hitting' if player['type'] == 'batter' else 'pitching'
                stats = statsapi.player_stat_data(player_id, group=group, type='season', sportId=1)
                if 'stats' in stats and stats['stats']:
                    row = stats['stats'][0]
                    mapping = batter_map if player['type'] == 'batter' else pitcher_map
                    scoring = batter_scoring if player['type'] == 'batter' else pitcher_scoring
                    historical_points = sum(row.get(mapping.get(stat, ''), 0) * coeff for stat, coeff in scoring.items())
                else:
                    historical_points = 0
                
# Projections: scrape FanGraphs Steamer for this player
projection_points = 0
try:
    # Build FanGraphs search URL (adjust for batter/pitcher)
    fg_pos = 'all' if player['type'] == 'batter' else 'pitching'
    search_name = player['name'].lower().replace(' ', '-').replace('.', '')
    fg_url = f"https://www.fangraphs.com/players/{search_name}/stats?position={fg_pos.upper()}"
    
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    response = requests.get(fg_url, headers=headers, timeout=10)
    
    if response.status_code == 200:
        soup = BeautifulSoup(response.text, 'html.parser')
        # Find the Steamer projection row in the dashboard table
        dashboard = soup.find('div', id='Dashboard')
        if dashboard:
            rows = dashboard.find_all('tr')
            for row in rows:
                if 'Steamer' in row.text:
                    cols = row.find_all('td')
                    if len(cols) > 10:
                        if player['type'] == 'batter':
                            # FanGraphs batter Steamer columns (approximate indices - verify on page)
                            pa = float(cols[2].text.strip() or 0)   # Plate Appearances
                            r = float(cols[4].text.strip() or 0)    # Runs
                            hr = float(cols[7].text.strip() or 0)   # HR
                            rbi = float(cols[8].text.strip() or 0)  # RBI
                            sb = float(cols[9].text.strip() or 0)   # SB
                            bb = float(cols[11].text.strip() or 0)  # BB
                            so = float(cols[12].text.strip() or 0)  # SO
                            projection_points = (
                                r * batter_scoring.get('R', 0) +
                                hr * batter_scoring.get('HR', 0) +
                                rbi * batter_scoring.get('RBI', 0) +
                                sb * batter_scoring.get('SB', 0) +
                                bb * batter_scoring.get('BB', 0) +
                                so * batter_scoring.get('SO', 0)
                                # Add more stats as needed (1B/2B/3B require calculated)
                            )
                        else:  # pitcher
                            ip = float(cols[4].text.strip() or 0)
                            w = float(cols[6].text.strip() or 0)
                            sv = float(cols[8].text.strip() or 0)
                            so = float(cols[10].text.strip() or 0)
                            bb = float(cols[11].text.strip() or 0)
                            er = float(cols[13].text.strip() or 0)
                            projection_points = (
                                w * pitcher_scoring.get('W', 0) +
                                sv * pitcher_scoring.get('SV', 0) +
                                ip * pitcher_scoring.get('IP', 0) +
                                so * pitcher_scoring.get('SO', 0) +
                                bb * pitcher_scoring.get('BB', 0) +
                                er * pitcher_scoring.get('ER', 0)
                            )
except Exception as e:
    st.warning(f"Projections failed for {player['name']}: {str(e)}")
    projection_points = 0
                
                # Matchups (for batters only)
    matchup_bonus = 0
                if player['type'] == 'batter':
                    # Get scheduled game for player's team
                    team_id = search[0].get('currentTeam', {}).get('id')
                    schedule = statsapi.schedule(date=date.today().strftime('%Y-%m-%d'), team=team_id)
                    if schedule:
                        game = schedule[0]
                        opp_pitcher_id = game['away_pitcher'] if game['home_id'] == team_id else game['home_pitcher']
                        if opp_pitcher_id:
                            opp_stats = statsapi.player_stat_data(opp_pitcher_id, group='pitching', type='season')
                            if 'stats' in opp_stats and opp_stats['stats']:
                                opp_row = opp_stats['stats'][0]
                                # Simple bonus: if opp ERA > 4, +10% points; if < 3, -10%
                                opp_era = opp_row.get('era', 0)
                                if opp_era > 4:
                                    matchup_bonus = 0.1 * projection_points
                                elif opp_era < 3:
                                    matchup_bonus = -0.1 * projection_points
                
                # Hot Streak / Bad Luck (advanced metrics)
                advanced_bonus = 0
                if player['type'] == 'batter':
                    # Hot streak: high BABIP, wOBA > xwOBA (good luck), or vice versa for bad luck
                    advanced = pb.statcast_batter(year, year, player_id)
                    if not advanced.empty:
                        recent_babip = advanced['babip'].mean()
                        woba = advanced['woba'].mean()
                        xwoba = advanced['xwoba'].mean()
                        if woba > xwoba + 0.03:  # Good luck/hot
                            advanced_bonus = 0.1 * projection_points
                        elif woba < xwoba - 0.03:  # Bad luck, due for regression
                            advanced_bonus = 0.15 * projection_points  # Bigger boost for bad luck
                
                player['points'] = historical_points + projection_points + matchup_bonus + advanced_bonus

            if unmatched:
                st.warning(f"No data for: {', '.join(unmatched)}")

            # ────────────────────────────────────────────────
            # Optimization
            # ────────────────────────────────────────────────
            hitters = [p for p in roster if p['type'] == 'batter' and 'IL' not in p['positions']]
            pitchers = [p for p in roster if p['type'] == 'pitcher' and 'IL' not in p['positions']]

            # Slots & Eligibility
            hitter_slots = {'C': 1, '1B': 1, '2B': 1, '3B': 1, 'SS': 1, 'OF': 3, 'UTIL': 2}
            pitcher_slots = {'SP': 2, 'RP': 2, 'P': 4}
            bn_slots = 5
            il_slots = 4  # Display IL separately

            # Eligibility (based on user input)
            hitter_eligible = {
                'C': ['C'], '1B': ['1B'], '2B': ['2B'], '3B': ['3B'], 'SS': ['SS'], 'OF': ['OF'],
                'UTIL': ['C', '1B', '2B', '3B', 'SS', 'OF', 'UTIL']
            }
            pitcher_eligible = {'SP': ['SP'], 'RP': ['RP'], 'P': ['SP', 'RP', 'P']}

            # Optimize function
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

# ────────────────────────────────────────────────
# Reset Roster Button
# ────────────────────────────────────────────────
if st.button("Reset Roster"):
    st.session_state.roster = []
    st.experimental_rerun()
